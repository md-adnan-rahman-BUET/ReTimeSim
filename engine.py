"""
ReTimeSim engine - everything except the user interface.

Builds the synthetic road network and population, turns an institutional schedule into
car trips, runs SUMO through TraCI while measuring the traffic, and computes congestion
metrics. The Streamlit app (app.py) calls these functions.

    python engine.py      rebuilds the model and the baseline results shipped with the app
"""
import json
import os
import random
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ============================================================
# Folders and files
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "model"            # network, zones, commuters (built once)
RESULTS_DIR = BASE_DIR / "results"        # baseline results shipped with the app
BASELINE_DIR = RESULTS_DIR / "baseline"
RUNS_DIR = BASE_DIR / "runs"              # one folder per user run

NETWORK_FILE = MODEL_DIR / "network.net.xml"
ZONES_FILE = MODEL_DIR / "zones.csv"
ZONE_EDGES_FILE = MODEL_DIR / "zone_edges.csv"
PEOPLE_FILE = MODEL_DIR / "people.csv"

# ============================================================
# Settings
# ============================================================
SIM_BEGIN, SIM_END = "06:00", "21:00"     # SUMO time = seconds after midnight
MIDDAY = "13:00"                          # trips before this count as morning
RANDOM_SEED = 42
STEP_LENGTH = 1.0

GROUPS = ["school", "industry", "government", "private", "university"]
GROUP_LABELS = {"school": "School", "industry": "Industry", "government": "Government",
                "private": "Private office", "university": "University"}
GROUP_ICONS = {"school": "🏫", "industry": "🏭", "government": "🏛️", "private": "🏢", "university": "🎓"}
ZONE_TYPE_LABELS = {"residential": "Homes", **GROUP_LABELS}
COLORS = {"residential": "#2a78d6", "school": "#eb6834", "industry": "#1baf7a",
          "government": "#eda100", "private": "#e87ba4", "university": "#008300"}

BASELINE = {
    "school":     {"start": "09:00", "end": "16:00"},
    "industry":   {"start": "08:00", "end": "17:00"},
    "government": {"start": "09:00", "end": "17:00"},
    "private":    {"start": "09:00", "end": "17:00"},
    "university": {"start": "08:00", "end": "17:00"},
}
EARLIEST_START, LATEST_START = "06:30", "11:00"    # slider range

TOTAL_COMMUTERS = 5000
GROUP_SHARES = {"school": 0.25, "industry": 0.20, "government": 0.15, "private": 0.25, "university": 0.15}
PLANNED_TRAVEL_FACTOR = 1.5      # people allow 1.5 x free-flow travel time
HABITS = {   # minutes arriving before the start / staying after the end (mean, spread)
    "school":     {"early_mean": 15, "early_sd": 7,  "late_mean": 5,  "late_sd": 5},
    "industry":   {"early_mean": 15, "early_sd": 5,  "late_mean": 5,  "late_sd": 3},
    "government": {"early_mean": 10, "early_sd": 8,  "late_mean": 15, "late_sd": 10},
    "private":    {"early_mean": 10, "early_sd": 8,  "late_mean": 30, "late_sd": 20},
    "university": {"early_mean": 10, "early_sd": 10, "late_mean": 20, "late_sd": 20},
}

LEFT_HAND_TRAFFIC = True
GRID_SIZE = 5
BLOCK_LENGTH = 400
ARTERIAL_INDEX = GRID_SIZE // 2
ROAD_CLASSES = {"arterial": {"lanes": 2, "speed": 60 / 3.6, "priority": 3},
                "local":    {"lanes": 1, "speed": 40 / 3.6, "priority": 1}}
B = BLOCK_LENGTH
ZONES = [
    {"zone": "R_A", "type": "residential", "x": 0 * B, "y": 4 * B, "radius": 1.6 * B},
    {"zone": "R_B", "type": "residential", "x": 4 * B, "y": 4 * B, "radius": 1.6 * B},
    {"zone": "R_C", "type": "residential", "x": 0 * B, "y": 0 * B, "radius": 1.6 * B},
    {"zone": "GOV", "type": "government",  "x": 1 * B, "y": 3 * B, "radius": 0.55 * B},
    {"zone": "PRV", "type": "private",     "x": 3 * B, "y": 3 * B, "radius": 0.55 * B},
    {"zone": "SCH", "type": "school",      "x": 2 * B, "y": 2 * B, "radius": 0.55 * B},
    {"zone": "UNI", "type": "university",  "x": 3 * B, "y": 1 * B, "radius": 0.55 * B},
    {"zone": "IND", "type": "industry",    "x": 1 * B, "y": 1 * B, "radius": 0.55 * B},
]

REROUTING = True
TIME_TO_TELEPORT = 300
MONITOR_INTERVAL = 60                     # seconds between traffic measurements
REPLAY_BEGIN, REPLAY_END = "07:00", "10:30"
REPLAY_INTERVAL = 20                      # seconds between recorded car positions
GUI_BEGIN, GUI_END = "06:30", "10:30"
GUI_DELAY_MS = 20


# ============================================================
# Time helpers
# ============================================================
def parse_clock(text):
    """'7:30' -> minutes after midnight (450)."""
    hours, minutes = str(text).strip().split(":")
    return int(hours) * 60 + int(minutes)


def minutes_to_clock(minutes):
    minutes = int(round(minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def clock_to_sim(text):
    return parse_clock(text) * 60


def sim_to_clock(seconds, with_seconds=False):
    hours, rest = divmod(int(round(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if with_seconds else f"{hours:02d}:{minutes:02d}"


SIM_BEGIN_S, SIM_END_S = clock_to_sim(SIM_BEGIN), clock_to_sim(SIM_END)


# ============================================================
# Schedules
# ============================================================
def duration_minutes(times):
    return parse_clock(times["end"]) - parse_clock(times["start"])


def schedule_from_starts(starts):
    """New start times; each group keeps its baseline working hours."""
    return {g: {"start": starts[g],
                "end": minutes_to_clock(parse_clock(starts[g]) + duration_minutes(BASELINE[g]))}
            for g in GROUPS}


def validate_schedule(schedule):
    problems = []
    for g in GROUPS:
        start, end = parse_clock(schedule[g]["start"]), parse_clock(schedule[g]["end"])
        if start < parse_clock(SIM_BEGIN) or end > parse_clock(SIM_END):
            problems.append(f"{GROUP_LABELS[g]} must start and end between {SIM_BEGIN} and {SIM_END}.")
    return problems


def schedule_text(schedule):
    return " | ".join(f"{g} {schedule[g]['start']}-{schedule[g]['end']}" for g in GROUPS)


def starts_by_time(schedule):
    """{'08:00': ['industry', 'university'], ...} - which groups start together."""
    by_time = {}
    for g in GROUPS:
        by_time.setdefault(schedule[g]["start"], []).append(g)
    return dict(sorted(by_time.items()))


# ============================================================
# SUMO setup
# ============================================================
def setup_sumo():
    """Find SUMO (installed, or the pip package eclipse-sumo) and put it on the PATH."""
    home = os.environ.get("SUMO_HOME")
    if not (home and shutil.which("sumo", path=os.path.join(home, "bin"))):
        try:
            import sumo                      # pip package eclipse-sumo
            home = sumo.SUMO_HOME
        except ImportError:
            exe = shutil.which("sumo")
            if exe is None:
                raise RuntimeError("SUMO not found. Install it from https://eclipse.dev/sumo/ "
                                   "or run: pip install eclipse-sumo")
            home = str(Path(exe).resolve().parent.parent)
    os.environ["SUMO_HOME"] = home
    bin_dir = os.path.join(home, "bin")
    if bin_dir not in os.environ["PATH"].split(os.pathsep):
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ["PATH"]

    check = subprocess.run(["sumo", "--version"], capture_output=True, text=True)
    if check.returncode != 0 or not check.stdout.strip():
        # On Linux servers this usually means missing system libraries (see packages.txt)
        raise RuntimeError(f"SUMO was found in {home} but could not start:\n{check.stderr.strip()}")
    version = check.stdout.splitlines()[0]
    has_screen = sys.platform in ("win32", "darwin") or bool(os.environ.get("DISPLAY"))
    gui = shutil.which("sumo-gui") is not None and has_screen
    return {"home": home, "version": version.replace("Eclipse SUMO sumo ", "SUMO "), "gui": gui}


# ============================================================
# Model: network, zones, commuters
# ============================================================
def node_id(i, j):
    return f"n{i}_{j}"


def road_class(i1, j1, i2, j2):
    if (j1 == j2 == ARTERIAL_INDEX) or (i1 == i2 == ARTERIAL_INDEX):
        return "arterial"
    return "local"


def build_network():
    """5 x 5 grid as plain XML -> netconvert -> model/network.net.xml"""
    folder = MODEL_DIR / "synthetic"
    folder.mkdir(parents=True, exist_ok=True)
    nodes, edges = ET.Element("nodes"), ET.Element("edges")
    for i in range(GRID_SIZE):
        for j in range(GRID_SIZE):
            on_arterial = ARTERIAL_INDEX in (i, j)
            ET.SubElement(nodes, "node", {"id": node_id(i, j), "x": str(i * B), "y": str(j * B),
                                          "type": "traffic_light" if on_arterial else "priority"})
    for i in range(GRID_SIZE):
        for j in range(GRID_SIZE):
            for i2, j2 in [(i + 1, j), (i, j + 1)]:
                if i2 >= GRID_SIZE or j2 >= GRID_SIZE:
                    continue
                road = ROAD_CLASSES[road_class(i, j, i2, j2)]
                for a, b in [((i, j), (i2, j2)), ((i2, j2), (i, j))]:
                    ET.SubElement(edges, "edge", {
                        "id": f"{node_id(*a)}_to_{node_id(*b)}", "from": node_id(*a), "to": node_id(*b),
                        "numLanes": str(road["lanes"]), "speed": f"{road['speed']:.2f}",
                        "priority": str(road["priority"])})
    ET.ElementTree(nodes).write(folder / "grid.nod.xml", encoding="utf-8", xml_declaration=True)
    ET.ElementTree(edges).write(folder / "grid.edg.xml", encoding="utf-8", xml_declaration=True)
    result = subprocess.run(["netconvert", "--node-files", str(folder / "grid.nod.xml"),
                             "--edge-files", str(folder / "grid.edg.xml"),
                             "--output-file", str(NETWORK_FILE),
                             "--lefthand", str(LEFT_HAND_TRAFFIC).lower(),
                             "--tls.default-type", "static"], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"netconvert failed:\n{result.stderr}")


def assign_zones(net):
    """Each car-usable edge goes to the nearest zone whose radius contains its midpoint."""
    from sumolib import geomhelper
    rows = []
    for edge in net.getEdges():
        if not edge.allows("passenger"):
            continue
        shape = edge.getShape()
        mx, my = geomhelper.positionAtShapeOffset(shape, geomhelper.polyLength(shape) / 2)
        best = None
        for z in ZONES:
            distance = ((mx - z["x"]) ** 2 + (my - z["y"]) ** 2) ** 0.5
            if distance <= z["radius"] and (best is None or distance < best[1]):
                best = (z["zone"], distance)
        if best:
            rows.append({"zone": best[0], "edge_id": edge.getID()})
    pd.DataFrame(ZONES).to_csv(ZONES_FILE, index=False)
    pd.DataFrame(rows).to_csv(ZONE_EDGES_FILE, index=False)


def split_total(total, shares):
    """Shares -> whole numbers adding up exactly to total (largest remainder)."""
    exact = {k: total * v / sum(shares.values()) for k, v in shares.items()}
    counts = {k: int(v) for k, v in exact.items()}
    for k in sorted(exact, key=lambda k: exact[k] - counts[k], reverse=True)[:total - sum(counts.values())]:
        counts[k] += 1
    return counts


def build_population():
    rng = random.Random(RANDOM_SEED)
    homes = [z["zone"] for z in ZONES if z["type"] == "residential"]
    rows = []
    for group, count in split_total(TOTAL_COMMUTERS, GROUP_SHARES).items():
        workplaces = [z["zone"] for z in ZONES if z["type"] == group]
        for _ in range(count):
            rows.append({"group": group, "home_zone": rng.choice(homes), "work_zone": rng.choice(workplaces)})
    people = pd.DataFrame(rows)
    people.insert(0, "person_id", [f"P{i:05d}" for i in range(1, len(people) + 1)])
    people.to_csv(PEOPLE_FILE, index=False)


class Model:
    """The fixed part of the experiment: road network, zones and commuters."""

    def __init__(self):
        import sumolib
        self.net = sumolib.net.readNet(str(NETWORK_FILE))
        self.zones = pd.read_csv(ZONES_FILE)
        self.zone_edges = pd.read_csv(ZONE_EDGES_FILE).groupby("zone")["edge_id"].apply(list).to_dict()
        self.people = pd.read_csv(PEOPLE_FILE, dtype={"person_id": str})
        self.free_flow_cache = {}

    def pick_edge(self, zone, person_id):
        """A road inside the zone - random, but always the same one for the same person."""
        return random.Random(f"{RANDOM_SEED}-{person_id}-{zone}").choice(self.zone_edges[zone])

    def free_flow_seconds(self, from_edge, to_edge):
        key = (from_edge, to_edge)
        if key not in self.free_flow_cache:
            path, _ = self.net.getFastestPath(self.net.getEdge(from_edge), self.net.getEdge(to_edge),
                                              vClass="passenger")
            self.free_flow_cache[key] = None if path is None else sum(e.getLength() / e.getSpeed() for e in path)
        return self.free_flow_cache[key]

    def facts(self):
        return {"commuters": len(self.people), "trips": 2 * len(self.people),
                "junctions": len(self.net.getNodes()),
                "signals": len(self.net.getTrafficLights()),
                "road_km": sum(e.getLength() for e in self.net.getEdges()) / 1000}


def build_model(log=print):
    """Build whatever is missing, then load the model."""
    MODEL_DIR.mkdir(exist_ok=True)
    if not NETWORK_FILE.exists():
        log("Building the road network with netconvert...")
        build_network()
    if not (ZONES_FILE.exists() and ZONE_EDGES_FILE.exists()):
        import sumolib
        log("Matching zones to roads...")
        assign_zones(sumolib.net.readNet(str(NETWORK_FILE)))
    if not PEOPLE_FILE.exists():
        log(f"Creating {TOTAL_COMMUTERS:,} commuters...")
        build_population()
    return Model()


# ============================================================
# Trips
# ============================================================
def person_habits(person_id, group):
    rng = random.Random(f"{RANDOM_SEED}-{person_id}-habits")
    h = HABITS[group]
    return max(0.0, rng.gauss(h["early_mean"], h["early_sd"])), max(0.0, rng.gauss(h["late_mean"], h["late_sd"]))


def generate_trips(model, schedule):
    """Morning (home -> work) and evening (work -> home) trip for every commuter."""
    rows = []
    for p in model.people.itertuples(index=False):
        home_edge, work_edge = model.pick_edge(p.home_zone, p.person_id), model.pick_edge(p.work_zone, p.person_id)
        early, late = person_habits(p.person_id, p.group)
        times = schedule[p.group]
        legs = []
        to_work = model.free_flow_seconds(home_edge, work_edge)
        if to_work is not None:
            depart = clock_to_sim(times["start"]) - early * 60 - PLANNED_TRAVEL_FACTOR * to_work
            legs.append(("am", "to_work", home_edge, work_edge, depart))
        if model.free_flow_seconds(work_edge, home_edge) is not None:
            legs.append(("pm", "to_home", work_edge, home_edge, clock_to_sim(times["end"]) + late * 60))
        for suffix, purpose, from_edge, to_edge, depart in legs:
            depart = int(round(depart))
            if SIM_BEGIN_S <= depart < SIM_END_S:
                rows.append({"trip_id": f"{p.person_id}_{suffix}", "person_id": p.person_id, "group": p.group,
                             "purpose": purpose, "from_edge": from_edge, "to_edge": to_edge, "depart": depart})
    return pd.DataFrame(rows).sort_values("depart", kind="stable").reset_index(drop=True)


def sumo_color(hex_color):
    return ",".join(str(int(hex_color[i:i + 2], 16)) for i in (1, 3, 5))


def write_routes(trips, path):
    routes = ET.Element("routes")
    for g in GROUPS:   # one vehicle type per group, coloured as on the map
        ET.SubElement(routes, "vType", {"id": g, "vClass": "passenger", "color": sumo_color(COLORS[g])})
    for t in trips.itertuples(index=False):
        ET.SubElement(routes, "trip", {"id": t.trip_id, "type": t.group, "depart": str(t.depart),
                                       "from": t.from_edge, "to": t.to_edge,
                                       "departLane": "best", "departSpeed": "max"})
    ET.indent(routes)
    ET.ElementTree(routes).write(path, encoding="utf-8", xml_declaration=True)


def write_config(run_dir):
    """SUMO configuration with paths relative to the run folder (so it works on any computer)."""
    config = ET.Element("configuration")
    sections = {
        "input": {"net-file": os.path.relpath(NETWORK_FILE, run_dir), "route-files": "trips.rou.xml"},
        "time": {"begin": SIM_BEGIN_S, "end": SIM_END_S, "step-length": STEP_LENGTH},
        "processing": {"time-to-teleport": TIME_TO_TELEPORT, "ignore-route-errors": "true"},
        "routing": {"device.rerouting.probability": 1 if REROUTING else 0, "device.rerouting.period": 120},
        "report": {"no-step-log": "true", "no-warnings": "true"},
    }
    for section, options in sections.items():
        element = ET.SubElement(config, section)
        for name, value in options.items():
            ET.SubElement(element, name, value=str(value))
    ET.indent(config)
    path = run_dir / "simulation.sumocfg"
    ET.ElementTree(config).write(path, encoding="utf-8", xml_declaration=True)
    return path


# ============================================================
# Simulation (TraCI)
# ============================================================
def run_simulation(model, schedule, run_dir, on_stage=None, on_progress=None):
    """Trips -> SUMO (headless, full day) with live measurements -> saved results.

    on_stage(text) reports what is happening; on_progress(now, sample) is called every
    simulated minute. Each run uses its own TraCI connection, so several users can run at once.
    """
    import traci
    import traci.constants as tc

    stage = on_stage or (lambda text: None)
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    stage(f"Turning the schedule into departure times for {len(model.people):,} commuters...")
    trips = generate_trips(model, schedule)
    trips.to_csv(run_dir / "trips.csv", index=False)
    write_routes(trips, run_dir / "trips.rou.xml")
    config = write_config(run_dir)
    (run_dir / "schedule.json").write_text(json.dumps(schedule, indent=2))

    stage(f"Starting SUMO and loading {len(trips):,} car trips...")
    label = f"retimesim-{uuid.uuid4().hex[:8]}"
    traci.start(["sumo", "-c", str(config),
                 "--tripinfo-output", str(run_dir / "tripinfo.xml"),
                 "--tripinfo-output.write-unfinished", "true",
                 "--statistic-output", str(run_dir / "statistics.xml")], label=label)
    conn = traci.getConnection(label)

    samples, positions = [], []
    try:
        stage(f"Simulating the day {SIM_BEGIN}-{SIM_END}, measuring the network every minute...")
        edges = [e for e in conn.edge.getIDList() if not e.startswith(":")]
        for e in edges:
            conn.edge.subscribe(e, [tc.LAST_STEP_VEHICLE_NUMBER, tc.LAST_STEP_MEAN_SPEED,
                                    tc.LAST_STEP_VEHICLE_HALTING_NUMBER])
        (xmin, ymin), (xmax, ymax) = conn.simulation.getNetBoundary()
        junction = next(j for j in conn.junction.getIDList() if not j.startswith(":"))
        conn.junction.subscribeContext(junction, tc.CMD_GET_VEHICLE_VARIABLE,
                                       2 * ((xmax - xmin) ** 2 + (ymax - ymin) ** 2) ** 0.5,
                                       [tc.VAR_POSITION, tc.VAR_TYPE])

        replay_begin, replay_end = clock_to_sim(REPLAY_BEGIN), clock_to_sim(REPLAY_END)
        last_depart = int(trips["depart"].max())
        next_sample = SIM_BEGIN_S
        while True:
            now = conn.simulation.getTime()
            if now >= next_sample:
                results = conn.edge.getAllSubscriptionResults()
                on_edges = halting = max_queue = 0
                speed_sum = 0.0
                for e in edges:
                    values = results.get(e, {})
                    count = values.get(tc.LAST_STEP_VEHICLE_NUMBER, 0)
                    stopped = values.get(tc.LAST_STEP_VEHICLE_HALTING_NUMBER, 0)
                    on_edges += count
                    halting += stopped
                    speed_sum += count * values.get(tc.LAST_STEP_MEAN_SPEED, 0.0)
                    max_queue = max(max_queue, stopped)
                sample = {"time": int(now), "clock": sim_to_clock(now),
                          "vehicles": conn.vehicle.getIDCount(),
                          "mean_speed_kmh": round(speed_sum / on_edges * 3.6, 1) if on_edges else None,
                          "halting": halting, "max_queue": max_queue}
                samples.append(sample)
                next_sample += MONITOR_INTERVAL
                if on_progress:
                    on_progress(now, sample)
            if replay_begin <= now <= replay_end and int(now) % REPLAY_INTERVAL == 0:
                for values in (conn.junction.getContextSubscriptionResults(junction) or {}).values():
                    x, y = values[tc.VAR_POSITION]
                    positions.append((int(now), round(x), round(y), values[tc.VAR_TYPE]))
            if now >= SIM_END_S or (now > last_depart and conn.simulation.getMinExpectedNumber() == 0):
                break
            conn.simulationStep(now + REPLAY_INTERVAL)
    finally:
        conn.close()

    stage("Calculating congestion metrics from SUMO's trip records...")
    timeseries = pd.DataFrame(samples)
    timeseries.to_csv(run_dir / "timeseries.csv", index=False)
    pd.DataFrame(positions, columns=["time", "x", "y", "group"]).to_csv(run_dir / "positions.csv.gz", index=False)
    metrics = compute_metrics(read_tripinfo(run_dir / "tripinfo.xml"),
                              read_statistics(run_dir / "statistics.xml"), timeseries)
    metrics["schedule"] = schedule_text(schedule)
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    return load_results(run_dir)


def read_tripinfo(path):
    rows = []
    for t in ET.parse(path).getroot().iter("tripinfo"):
        rows.append({"group": t.get("vType"), "depart": float(t.get("depart")),
                     "arrival": float(t.get("arrival")), "duration": float(t.get("duration")),
                     "route_length": float(t.get("routeLength")), "waiting": float(t.get("waitingTime")),
                     "time_loss": float(t.get("timeLoss"))})
    return pd.DataFrame(rows)


def read_statistics(path):
    root = ET.parse(path).getroot()
    return {"loaded": int(root.find("vehicles").get("loaded")),
            "teleports": int(root.find("teleports").get("total"))}


def mean_minutes(values):
    return round(float(values.mean()) / 60, 2) if len(values) else None


def compute_metrics(trip_info, statistics, timeseries):
    done = trip_info[trip_info["arrival"] >= 0]
    midday = clock_to_sim(MIDDAY)
    peak = timeseries.loc[timeseries["vehicles"].idxmax()]
    metrics = {
        "trips": statistics["loaded"],
        "completed": int(len(done)),
        "teleports": statistics["teleports"],
        "avg_travel_time_min": mean_minutes(done["duration"]),
        "avg_delay_min": mean_minutes(done["time_loss"]),
        "morning_avg_delay_min": mean_minutes(done.loc[done["depart"] < midday, "time_loss"]),
        "evening_avg_delay_min": mean_minutes(done.loc[done["depart"] >= midday, "time_loss"]),
        "total_delay_veh_h": round(float(done["time_loss"].sum()) / 3600, 1),
        "avg_waiting_min": mean_minutes(done["waiting"]),
        "avg_speed_kmh": round(float(done["route_length"].sum() / done["duration"].sum()) * 3.6, 1),
        "peak_vehicles": int(peak["vehicles"]),
        "peak_time": str(peak["clock"]),
        "max_queue": int(timeseries["max_queue"].max()),
    }
    for g in GROUPS:
        metrics[f"delay_{g}_min"] = mean_minutes(done.loc[done["group"] == g, "time_loss"])
    return metrics


def load_results(run_dir):
    run_dir = Path(run_dir)
    return {"dir": run_dir,
            "metrics": json.loads((run_dir / "metrics.json").read_text()),
            "schedule": json.loads((run_dir / "schedule.json").read_text()),
            "timeseries": pd.read_csv(run_dir / "timeseries.csv"),
            "positions": pd.read_csv(run_dir / "positions.csv.gz"),
            "trips": pd.read_csv(run_dir / "trips.csv", usecols=["group", "depart"])}


def has_results(run_dir):
    return all((Path(run_dir) / f).exists() for f in
               ["metrics.json", "schedule.json", "timeseries.csv", "positions.csv.gz", "trips.csv"])


def new_run_dir():
    return RUNS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def clean_old_runs(max_age_hours=12):
    """Delete user runs older than max_age_hours (keeps a hosted app from filling its disk)."""
    if not RUNS_DIR.exists():
        return
    limit = time.time() - max_age_hours * 3600
    for folder in RUNS_DIR.iterdir():
        if folder.is_dir() and folder.stat().st_mtime < limit:
            shutil.rmtree(folder, ignore_errors=True)


def launch_sumo_gui(run_dir):
    """Open SUMO-GUI for the morning peak of a finished run (does not block)."""
    run_dir = Path(run_dir)
    config = write_config(run_dir)
    return subprocess.Popen(["sumo-gui", "-c", str(config),
                             "--begin", str(clock_to_sim(GUI_BEGIN)), "--end", str(clock_to_sim(GUI_END)),
                             "--start", "true", "--delay", str(GUI_DELAY_MS),
                             "--human-readable-time", "true"], cwd=run_dir)


# ============================================================
# Replay animation (GIF)
# ============================================================
def make_replay_gif(model, positions_by_name, colors_by_name, file, frame_step=60, fps=8, dpi=72):
    """Cars on the network, scenarios side by side at the same clock time."""
    from matplotlib.animation import FuncAnimation, PillowWriter
    from matplotlib.lines import Line2D

    names = list(positions_by_name)
    frames = {n: dict(tuple(positions_by_name[n].groupby("time"))) for n in names}
    times = list(range(clock_to_sim(REPLAY_BEGIN), clock_to_sim(REPLAY_END) + 1, frame_step))

    fig, axes = plt.subplots(1, len(names), figsize=(4.4 * len(names), 5.0), squeeze=False)
    dots, titles = [], []
    for ax, name in zip(axes[0], names):
        for edge in model.net.getEdges():
            xs, ys = zip(*edge.getShape())
            ax.plot(xs, ys, color="#c3c2b7", linewidth=1 + edge.getLaneNumber(), solid_capstyle="round", zorder=1)
        ax.set_aspect("equal")
        ax.margins(0.04)
        ax.axis("off")
        titles.append(ax.set_title(name, fontsize=12, fontweight="bold", color=colors_by_name[name]))
        dots.append(ax.scatter([], [], s=10, linewidths=0, zorder=3))
    clock = fig.suptitle("", fontsize=15, fontweight="bold")
    fig.legend(handles=[Line2D([], [], marker="o", linestyle="", color=COLORS[g], label=GROUP_LABELS[g])
                        for g in GROUPS], loc="lower center", ncol=len(GROUPS), frameon=False, fontsize=9)
    fig.tight_layout(rect=(0, 0.06, 1, 0.95))

    def update(t):
        clock.set_text(f"Morning traffic   {sim_to_clock(t)}")
        for name, dot, title in zip(names, dots, titles):
            cars = frames[name].get(t)
            if cars is None or cars.empty:
                dot.set_offsets(np.empty((0, 2)))
                count = 0
            else:
                dot.set_offsets(cars[["x", "y"]].to_numpy())
                dot.set_color(cars["group"].map(COLORS).tolist())
                count = len(cars)
            title.set_text(f"{name}  ·  {count:,} cars on the road")
        return dots + titles + [clock]

    FuncAnimation(fig, update, frames=times).save(file, writer=PillowWriter(fps=fps), dpi=dpi)
    plt.close(fig)
    return Path(file)


# ============================================================
# Command line: rebuild the model and the shipped baseline results
# ============================================================
if __name__ == "__main__":
    print(setup_sumo())
    model = build_model()
    print("Model:", model.facts())
    for name, schedule, folder in [
        ("baseline", BASELINE, BASELINE_DIR),
        ("example staggered", schedule_from_starts(
            {"school": "07:30", "industry": "08:00", "government": "09:00", "private": "10:00", "university": "10:00"}),
         RESULTS_DIR / "example_staggered"),
    ]:
        started = time.time()
        results = run_simulation(model, schedule, folder, on_stage=print,
                                 on_progress=lambda now, s: print(f"  {s['clock']}  {s['vehicles']:5} vehicles")
                                 if int(now) % 3600 == 0 else None)
        m = results["metrics"]
        print(f"{name}: avg delay {m['avg_delay_min']} min, total delay {m['total_delay_veh_h']} veh-h "
              f"({time.time() - started:.0f} s)")
    baseline, example = load_results(BASELINE_DIR), load_results(RESULTS_DIR / "example_staggered")
    make_replay_gif(model, {"Baseline": baseline["positions"], "Staggered": example["positions"]},
                    {"Baseline": "#898781", "Staggered": "#2a78d6"}, RESULTS_DIR / "replay_example.gif")
    print("Replay GIF written")
