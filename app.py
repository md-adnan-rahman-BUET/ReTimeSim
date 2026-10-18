"""
ReTimeSim dashboard.

    streamlit run app.py

Pages: Overview -> Choose start times (one institute at a time) -> Review & submit -> Running -> Results
"""
import datetime as dt

import pandas as pd
import streamlit as st

import charts as C
import engine as E

st.set_page_config(page_title="ReTimeSim · Staggered schedules", page_icon="🚦", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 2.2rem; max-width: 1280px;}
.hero {padding: 2rem 2.4rem; border-radius: 18px; margin-bottom: 1.4rem;
       background: linear-gradient(120deg, #184f95 0%, #2a78d6 60%, #1baf7a 140%); color: #fff;}
.hero h1 {color: #fff; margin: 0; font-size: 2.6rem; line-height: 1.1;}
.hero p {color: rgba(255,255,255,.92); font-size: 1.12rem; margin: .6rem 0 0; max-width: 46rem;}
.hero .tag {display: inline-block; padding: .2rem .7rem; border-radius: 999px; font-size: .8rem;
            background: rgba(255,255,255,.18); margin-bottom: .8rem; letter-spacing: .02em;}
.inst-icon {font-size: 3.4rem; line-height: 1;}
.inst-name {font-size: 1.9rem; font-weight: 700; margin: .3rem 0 0;}
.big-time {font-size: 2.6rem; font-weight: 700; letter-spacing: .02em;}
.muted {opacity: .7;}
.step {padding: .35rem .6rem; border-radius: 8px; margin: .15rem 0; font-size: .95rem;}
.step.now {background: rgba(42,120,214,.15); font-weight: 600;}
.step.done {opacity: .75;}
</style>
""", unsafe_allow_html=True)


def show(fig):
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


# ============================================================
# Engine, model and baseline (prepared once, shared by all users)
# ============================================================
@st.cache_resource(show_spinner=False)
def get_sumo():
    return E.setup_sumo()


@st.cache_resource(show_spinner=False)
def get_model():
    E.clean_old_runs()
    return E.build_model(log=lambda message: None)


@st.cache_resource(show_spinner=False)
def get_baseline():
    if not E.has_results(E.BASELINE_DIR):
        E.run_simulation(get_model(), E.BASELINE, E.BASELINE_DIR)
    return E.load_results(E.BASELINE_DIR)


ss = st.session_state
if "ready" not in ss:
    with st.status("Preparing ReTimeSim...", expanded=True) as status:
        st.write("🔎 Looking for the SUMO traffic simulator...")
        sumo = get_sumo()
        st.write(f"✅ Found {sumo['version']}")
        st.write("🗺️ Loading the sample road network and 5,000 commuters...")
        model = get_model()
        st.write("📊 Loading the baseline simulation (today's schedule)...")
        baseline = get_baseline()
        status.update(label="ReTimeSim is ready", state="complete", expanded=False)
    ss.ready = True
sumo, model, baseline = get_sumo(), get_model(), get_baseline()

ss.setdefault("page", "home")
ss.setdefault("idx", 0)
ss.setdefault("starts", {g: E.BASELINE[g]["start"] for g in E.GROUPS})
ss.setdefault("run_dir", None)


def go(page, idx=None):
    ss.page = page
    if idx is not None:
        ss.idx = idx


def to_time(clock):
    hours, minutes = map(int, clock.split(":"))
    return dt.time(hours, minutes)


def shift_text(minutes):
    if minutes == 0:
        return "no change"
    hours, mins = divmod(abs(minutes), 60)
    text = (f"{hours} h " if hours else "") + (f"{mins} min" if mins else "")
    return f"{text.strip()} {'later' if minutes > 0 else 'earlier'}"


# ============================================================
# Sidebar
# ============================================================
with st.sidebar:
    st.markdown("## 🚦 ReTimeSim")
    st.caption("Re-timing institutions to tame rush hour")
    st.write("")
    steps = [("home", "Overview"), ("schedule", "Choose start times"), ("review", "Review & submit"),
             ("run", "Simulation"), ("results", "Results")]
    current = [key for key, _ in steps].index(ss.page)
    for i, (key, label) in enumerate(steps):
        state = "now" if i == current else ("done" if i < current else "")
        mark = "●" if i == current else ("✓" if i < current else "○")
        st.markdown(f"<div class='step {state}'>{mark}&nbsp; {label}</div>", unsafe_allow_html=True)
    st.divider()
    st.caption(f"Traffic engine: **{sumo['version']}**")
    st.caption("SUMO-GUI: " + ("available on this computer" if sumo["gui"] else "not available here"))
    with st.expander("About"):
        st.write("ReTimeSim builds a synthetic city, turns institutional start times into 10,000 car "
                 "trips and simulates every car with SUMO, controlled from Python through TraCI.")
        st.write("Made for Stanford's Code in Place.")


# ============================================================
# Page 1 - Overview
# ============================================================
def page_home():
    st.markdown("""
    <div class="hero">
      <span class="tag">PYTHON × SUMO TRAFFIC SIMULATION</span>
      <h1>What if the whole city didn't start work at the same time?</h1>
      <p>Schools, industries, offices and universities send thousands of commuters onto the same roads
      at 8 and 9 o'clock. Pick new start times for each one and ReTimeSim simulates every car to show
      what happens to rush hour.</p>
    </div>""", unsafe_allow_html=True)

    facts = model.facts()
    for col, (label, value) in zip(st.columns(4), [
            ("Commuters", f"{facts['commuters']:,}"), ("Car trips per day", f"{facts['trips']:,}"),
            ("Road network (km)", f"{facts['road_km']:.0f}"),
            ("Delay per trip today", f"{baseline['metrics']['avg_delay_min']:.1f} min")]):
        col.metric(label, value, border=True)

    left, right = st.columns([1.05, 1])
    with left:
        st.subheader("The sample city")
        st.caption("A 5 × 5 grid with left-hand traffic. Thick roads are 2-lane arterials with traffic signals. "
                   "People live in the three corner zones and work in the five coloured zones. Hover for details.")
        show(C.network_figure(model))
    with right:
        st.subheader("Today's schedule (baseline)")
        st.caption("Five institutions, two start times: everyone hits the road at once.")
        show(C.timeline_figure(E.BASELINE, height=290))
        together = E.starts_by_time(E.BASELINE)
        st.markdown("  \n".join(f"**{t}** — " + ", ".join(E.GROUP_LABELS[g] for g in groups)
                                for t, groups in together.items()))
        st.info(f"With this schedule, up to **{baseline['metrics']['peak_vehicles']:,} cars** are on the "
                f"road at once (at {baseline['metrics']['peak_time']}), and the city loses "
                f"**{baseline['metrics']['total_delay_veh_h']:,.0f} vehicle-hours** to congestion every day.")

    st.subheader("How it works")
    for col, (icon, title, text) in zip(st.columns(4), [
            ("🎚️", "1 · Choose", "Set a new start time for each institution, one at a time."),
            ("🧭", "2 · Generate", "Each commuter's departure follows from their start time and travel time."),
            ("🚗", "3 · Simulate", "SUMO moves all 10,000 cars through the city from 06:00 to 21:00."),
            ("📊", "4 · Compare", "See delay, queues and a replay of your schedule against the baseline.")]):
        with col.container(border=True):
            st.markdown(f"### {icon}\n**{title}**")
            st.caption(text)
    st.write("")
    st.button("Design a staggered schedule  →", type="primary", on_click=go, args=("schedule", 0))


# ============================================================
# Page 2 - one institute at a time
# ============================================================
def page_schedule():
    idx = ss.idx
    g = E.GROUPS[idx]
    st.progress((idx + 1) / len(E.GROUPS), text=f"Institute {idx + 1} of {len(E.GROUPS)}")
    st.header("Choose new start times")
    st.caption("Move the slider to set when this institution starts. Its working hours stay the same, "
               "so the end time moves with it. Nothing is simulated until you submit.")

    left, right = st.columns([1, 1.25], gap="large")
    key = f"start_{g}"
    if key not in ss:
        ss[key] = to_time(ss.starts[g])

    with left, st.container(border=True):
        count = int((model.people["group"] == g).sum())
        st.markdown(f"<div class='inst-icon'>{E.GROUP_ICONS[g]}</div>"
                    f"<div class='inst-name'>{E.GROUP_LABELS[g]}</div>", unsafe_allow_html=True)
        st.caption(f"{count:,} commuters · baseline {E.BASELINE[g]['start']}–{E.BASELINE[g]['end']} "
                   f"({E.duration_minutes(E.BASELINE[g]) // 60} h)")
        st.slider("Start time", min_value=to_time(E.EARLIEST_START), max_value=to_time(E.LATEST_START),
                  step=dt.timedelta(minutes=15), format="HH:mm", key=key)
        ss.starts[g] = ss[key].strftime("%H:%M")
        new = E.schedule_from_starts(ss.starts)[g]
        shift = E.parse_clock(new["start"]) - E.parse_clock(E.BASELINE[g]["start"])
        st.markdown(f"<div class='big-time'>{new['start']} – {new['end']}</div>"
                    f"<div class='muted'>{shift_text(shift) + ' than today' if shift else 'same as today'}</div>",
                    unsafe_allow_html=True)
        st.write("")

        def reset():
            ss[key] = to_time(E.BASELINE[g]["start"])
            ss.starts[g] = E.BASELINE[g]["start"]
        st.button("↺ Use today's time", on_click=reset)

    with right:
        st.markdown("##### Your schedule so far")
        show(C.timeline_figure(E.BASELINE, E.schedule_from_starts(ss.starts), highlight=g, height=340))

    back, _, forward = st.columns([1, 2, 1])
    if idx == 0:
        back.button("← Overview", on_click=go, args=("home",), width="stretch")
    else:
        back.button(f"← {E.GROUP_LABELS[E.GROUPS[idx - 1]]}", on_click=go, args=("schedule", idx - 1),
                    width="stretch")
    if idx < len(E.GROUPS) - 1:
        forward.button(f"{E.GROUP_LABELS[E.GROUPS[idx + 1]]} →", type="primary", on_click=go,
                       args=("schedule", idx + 1), width="stretch")
    else:
        forward.button("Review schedule →", type="primary", on_click=go, args=("review",),
                       width="stretch")


# ============================================================
# Page 3 - review & submit
# ============================================================
def page_review():
    schedule = E.schedule_from_starts(ss.starts)
    st.header("Review your schedule")
    st.caption("Check the new times. The simulation starts only when you press **Submit and run**.")

    left, right = st.columns([1, 1.2], gap="large")
    with left:
        rows = []
        for g in E.GROUPS:
            shift = E.parse_clock(schedule[g]["start"]) - E.parse_clock(E.BASELINE[g]["start"])
            rows.append({"Institute": f"{E.GROUP_ICONS[g]} {E.GROUP_LABELS[g]}",
                         "Today": f"{E.BASELINE[g]['start']}–{E.BASELINE[g]['end']}",
                         "Your schedule": f"{schedule[g]['start']}–{schedule[g]['end']}",
                         "Change": shift_text(shift)})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        busiest_today = max(len(v) for v in E.starts_by_time(E.BASELINE).values())
        busiest_yours = max(len(v) for v in E.starts_by_time(schedule).values())
        st.metric("Most institutions starting at the same time", busiest_yours,
                  f"{busiest_yours - busiest_today:+d} vs today", delta_color="inverse", border=True)
    with right:
        show(C.timeline_figure(E.BASELINE, schedule, height=330))

    problems = E.validate_schedule(schedule)
    for problem in problems:
        st.error(problem)
    if schedule == E.BASELINE:
        st.warning("This is the same as today's schedule - change at least one start time to see a difference.")

    st.info("⏱️ The simulation moves 10,000 cars through the whole day (06:00–21:00) and takes about a minute.")
    back, _, submit = st.columns([1, 2, 1])
    back.button("← Edit times", on_click=go, args=("schedule", 0), width="stretch")
    submit.button("▶ Submit and run", type="primary", on_click=go, args=("run",),
                  disabled=bool(problems), width="stretch")


# ============================================================
# Page 4 - running (tells the user what is happening)
# ============================================================
def page_run():
    schedule = E.schedule_from_starts(ss.starts)
    run_dir = E.new_run_dir()
    st.header("Simulating your schedule")
    st.caption("You can watch the simulated clock move. Please keep this page open.")

    status = st.status("Running the simulation...", expanded=True)
    # Streamlit keeps the previous page visible (faded) below the newest element until the run
    # ends; empty placeholders replace it, so old buttons cannot be clicked during the simulation.
    for _ in range(40):
        st.empty()
    with status:
        progress = st.progress(0.0, text="Preparing...")
        live = st.empty()

        def on_stage(text):
            st.write("▸ " + text)

        def on_progress(now, sample):
            fraction = (now - E.SIM_BEGIN_S) / (E.SIM_END_S - E.SIM_BEGIN_S)
            if int(now) % 300 == 0:   # refresh every 5 simulated minutes
                progress.progress(min(fraction, 1.0), text=f"Simulated clock: {sample['clock']}")
                speed = f"{sample['mean_speed_kmh']:.0f} km/h" if sample["mean_speed_kmh"] else "–"
                live.markdown(f"🕒 **{sample['clock']}** · 🚗 **{sample['vehicles']:,}** cars on the road · "
                              f"⚡ {speed} · 🛑 {sample['halting']:,} stopped")

        results = E.run_simulation(model, schedule, run_dir, on_stage=on_stage, on_progress=on_progress)
        progress.progress(1.0, text="Simulation finished")
        st.write("▸ Drawing the traffic replay: baseline and your schedule side by side...")
        E.make_replay_gif(model, {"Baseline": baseline["positions"], "Your schedule": results["positions"]},
                          C.SCENARIO_COLORS, run_dir / "replay.gif")
        st.write("✅ Done")
        status.update(label="Simulation complete", state="complete", expanded=False)

    ss.run_dir = str(run_dir)
    ss.page = "results"
    st.rerun()


# ============================================================
# Page 5 - results
# ============================================================
KPIS = [  # metric, label, format, lower is better
    ("avg_travel_time_min", "Average travel time", "{:.1f} min", True),
    ("avg_delay_min", "Average delay per trip", "{:.1f} min", True),
    ("avg_speed_kmh", "Average speed", "{:.1f} km/h", False),
    ("peak_vehicles", "Peak cars on the road", "{:,}", True),
    ("total_delay_veh_h", "Total delay per day", "{:,.0f} veh-h", True),
]
TABLE_ROWS = [
    ("avg_travel_time_min", "Average travel time (min)"), ("avg_delay_min", "Average delay (min)"),
    ("morning_avg_delay_min", "  morning (min)"), ("evening_avg_delay_min", "  evening (min)"),
    ("avg_waiting_min", "Average waiting (min)"), ("avg_speed_kmh", "Average speed (km/h)"),
    ("total_delay_veh_h", "Total delay (vehicle-hours)"), ("peak_vehicles", "Peak cars on the road"),
    ("max_queue", "Longest queue on one road (cars)"), ("teleports", "Cars removed by gridlock"),
]


def page_results():
    if not ss.run_dir or not E.has_results(ss.run_dir):
        st.warning("No results yet.")
        st.button("Design a schedule →", on_click=go, args=("schedule", 0))
        return
    mine = E.load_results(ss.run_dir)
    m, b = mine["metrics"], baseline["metrics"]

    st.header("Results: your schedule vs today")
    change = (b["total_delay_veh_h"] - m["total_delay_veh_h"]) / b["total_delay_veh_h"] * 100
    if change > 0:
        st.success(f"🎉 Your schedule cuts total delay by **{change:.1f}%**: from "
                   f"{b['total_delay_veh_h']:,.0f} to {m['total_delay_veh_h']:,.0f} vehicle-hours per day.")
    else:
        st.warning(f"Your schedule increases total delay by **{-change:.1f}%** "
                   f"({b['total_delay_veh_h']:,.0f} → {m['total_delay_veh_h']:,.0f} vehicle-hours). Try spreading "
                   "the start times further apart.")

    for col, (key, label, fmt, lower_better) in zip(st.columns(len(KPIS)), KPIS):
        pct = (m[key] - b[key]) / b[key] * 100 if b[key] else 0
        col.metric(label, fmt.format(m[key]), f"{pct:+.1f}% vs today",
                   delta_color="inverse" if lower_better else "normal", border=True)

    tabs = st.tabs(["🎬 Replay", "📈 Traffic over the day", "🚗 Departures", "👥 Delay by group",
                    "📋 All indicators", "🗓️ Schedules", "🗺️ Network"])
    with tabs[0]:
        st.caption("Morning traffic 07:00–10:30. Each dot is a car, coloured by where its driver is going.")
        gif = mine["dir"] / "replay.gif"
        if gif.exists():
            st.image(str(gif), width="stretch")
        st.markdown("##### Explore one moment")
        moments = list(range(E.clock_to_sim(E.REPLAY_BEGIN), E.clock_to_sim(E.REPLAY_END) + 1, 60))
        moment = st.select_slider("Time", options=moments, value=E.clock_to_sim(b["peak_time"])
                                  if E.clock_to_sim(b["peak_time"]) in moments else moments[len(moments) // 2],
                                  format_func=E.sim_to_clock)
        show(C.snapshot_figure(model, {"Baseline": baseline["positions"], "Your schedule": mine["positions"]},
                               moment))
    with tabs[1]:
        st.caption("Measured every minute while SUMO was running.")
        show(C.timeseries_figure({"Baseline": baseline["timeseries"], "Your schedule": mine["timeseries"]}))
    with tabs[2]:
        show(C.departures_figure({"Baseline": baseline["trips"], "Your schedule": mine["trips"]}))
    with tabs[3]:
        show(C.group_delay_figure({"Baseline": b, "Your schedule": m}))
    with tabs[4]:
        rows = []
        for key, label in TABLE_ROWS:
            base_value, my_value = b[key] or 0, m[key] or 0
            pct = (my_value - base_value) / base_value * 100 if base_value else 0
            rows.append({"Indicator": label, "Today": base_value, "Your schedule": my_value,
                         "Change": f"{pct:+.1f}%"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={"Today": st.column_config.NumberColumn(format="%.1f"),
                                    "Your schedule": st.column_config.NumberColumn(format="%.1f")})
    with tabs[5]:
        show(C.timeline_figure(E.BASELINE, mine["schedule"], height=330))
    with tabs[6]:
        show(C.network_figure(model))

    st.divider()
    left, right = st.columns(2)
    with left:
        st.markdown("##### 🖥️ Watch it live in SUMO-GUI")
        if sumo["gui"]:
            st.caption("Opens SUMO's own window for the morning peak (06:30–10:30), cars coloured by group.")
            a, c = st.columns(2)
            if a.button("Watch today's schedule", width="stretch"):
                E.launch_sumo_gui(E.BASELINE_DIR)
                st.toast("SUMO-GUI is opening in a new window...")
            if c.button("Watch your schedule", width="stretch"):
                E.launch_sumo_gui(mine["dir"])
                st.toast("SUMO-GUI is opening in a new window...")
        else:
            st.caption("Available when you run ReTimeSim on your own computer with SUMO installed "
                       "(see the README). The replay above shows the same traffic.")
    with right:
        st.markdown("##### Try something else")
        a, c = st.columns(2)
        a.button("🔁 Try another schedule", type="primary", on_click=go, args=("schedule", 0),
                 width="stretch")

        def start_over():
            ss.starts = {g: E.BASELINE[g]["start"] for g in E.GROUPS}
            for g in E.GROUPS:
                ss.pop(f"start_{g}", None)
            go("home")
        c.button("↺ Start over", on_click=start_over, width="stretch")


{"home": page_home, "schedule": page_schedule, "review": page_review,
 "run": page_run, "results": page_results}[ss.page]()
