"""Interactive Plotly charts for the ReTimeSim dashboard."""
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import engine as E

ROAD_GREY = "#c3c2b7"
SIGNAL_COLOR = "#52514e"
SCENARIO_COLORS = {"Baseline": "#898781", "Your schedule": "#2a78d6"}


def _clock_axis(fig, first=6, last=21, **where):
    fig.update_xaxes(tickvals=list(range(first, last + 1)),
                     ticktext=[f"{h:02d}:00" for h in range(first, last + 1)],
                     range=[first, last], **where)


def _layout(fig, height, legend=True):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=40, b=10), hovermode="closest",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0) if legend else None,
                      showlegend=legend)
    return fig


def _road_traces(model, showlegend=False):
    xs, ys = [], []
    for edge in model.net.getEdges():
        ex, ey = zip(*edge.getShape())
        xs += list(ex) + [None]
        ys += list(ey) + [None]
    return go.Scatter(x=xs, y=ys, mode="lines", line=dict(color=ROAD_GREY, width=3),
                      hoverinfo="skip", showlegend=showlegend, name="Roads")


def network_figure(model, height=470):
    """The sample city: roads coloured by zone, traffic signals, zone labels."""
    edge_zone = {e: z for z, edges in model.zone_edges.items() for e in edges}
    zone_type = dict(zip(model.zones["zone"], model.zones["type"]))
    lines = {}
    for edge in model.net.getEdges():
        zone = edge_zone.get(edge.getID())
        key = (zone_type[zone] if zone else "road", edge.getLaneNumber())
        ex, ey = zip(*edge.getShape())
        xs, ys = lines.setdefault(key, ([], []))
        xs += list(ex) + [None]
        ys += list(ey) + [None]

    fig = go.Figure()
    shown = set()
    for (kind, lanes), (xs, ys) in sorted(lines.items(), key=lambda item: item[0][0] != "road"):
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines", hoverinfo="skip", legendgroup=kind, showlegend=kind not in shown,
            name="Other roads" if kind == "road" else E.ZONE_TYPE_LABELS[kind],
            line=dict(color=E.COLORS.get(kind, ROAD_GREY), width=2.5 * lanes)))
        shown.add(kind)

    signals = [n.getCoord() for n in model.net.getNodes() if n.getType() == "traffic_light"]
    fig.add_trace(go.Scatter(x=[c[0] for c in signals], y=[c[1] for c in signals], mode="markers",
                             name="Traffic signal", hovertemplate="Traffic signal<extra></extra>",
                             marker=dict(symbol="square", size=9, color=SIGNAL_COLOR,
                                         line=dict(color="white", width=1))))

    lives = model.people["home_zone"].value_counts()
    works = model.people["work_zone"].value_counts()
    zones = model.zones
    hover = [f"<b>{r.zone}</b> · {E.ZONE_TYPE_LABELS[r.type]}<br>"
             + (f"{lives.get(r.zone, 0):,} commuters live here" if r.type == "residential"
                else f"{works.get(r.zone, 0):,} commuters work here")
             for r in zones.itertuples()]
    fig.add_trace(go.Scatter(x=zones["x"], y=zones["y"], mode="markers+text", text=zones["zone"],
                             textposition="top right", textfont=dict(size=13), hovertext=hover,
                             hoverinfo="text", showlegend=False,
                             marker=dict(size=14, color="rgba(0,0,0,0)")))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False, scaleanchor="x", scaleratio=1)
    return _layout(fig, height)


def timeline_figure(baseline, schedule=None, highlight=None, height=300):
    """Working hours of each institute: baseline (grey) and the new schedule (coloured)."""
    labels = [f"{E.GROUP_ICONS[g]} {E.GROUP_LABELS[g]}" for g in E.GROUPS][::-1]
    groups = E.GROUPS[::-1]

    def bar(sched, name, colors, opacity=1.0):
        starts = [E.parse_clock(sched[g]["start"]) / 60 for g in groups]
        lengths = [E.duration_minutes(sched[g]) / 60 for g in groups]
        text = [f"{sched[g]['start']}–{sched[g]['end']}" for g in groups]
        return go.Bar(y=labels, x=lengths, base=starts, orientation="h", name=name, text=text,
                      textposition="inside", insidetextanchor="middle", marker_color=colors,
                      opacity=opacity, hovertemplate="%{y}: %{text}<extra>" + name + "</extra>")

    fig = go.Figure()
    if schedule is None:
        fig.add_trace(bar(baseline, "Baseline", [E.COLORS[g] for g in groups]))
    else:
        fig.add_trace(bar(baseline, "Baseline", SCENARIO_COLORS["Baseline"], opacity=0.55))
        colors = [E.COLORS[g] if (highlight is None or g == highlight) else "#9ec5f4" for g in groups]
        fig.add_trace(bar(schedule, "Your schedule", colors))
        fig.update_layout(barmode="group", bargap=0.25, bargroupgap=0.08)
    _clock_axis(fig, 6, 21)
    fig.update_yaxes(showgrid=False)
    return _layout(fig, height, legend=schedule is not None)


def departures_figure(trips_by_name, height=340):
    fig = go.Figure()
    for name, trips in trips_by_name.items():
        counts = (trips["depart"] // 900).value_counts().reindex(
            range(E.SIM_BEGIN_S // 900, E.SIM_END_S // 900), fill_value=0)
        fig.add_trace(go.Scatter(x=[b / 4 for b in counts.index], y=counts.values, mode="lines",
                                 line=dict(shape="hv", width=2.5, color=SCENARIO_COLORS[name]), name=name,
                                 hovertemplate="%{y:,} departures<extra>" + name + "</extra>"))
    _clock_axis(fig)
    fig.update_yaxes(title="Departures per 15 min")
    fig.update_layout(title=dict(text="When do commuters leave home or work?", x=0, font=dict(size=15)))
    return _layout(fig, height)


def timeseries_figure(series_by_name, height=620):
    panels = [("vehicles", "Vehicles on the network"), ("mean_speed_kmh", "Mean speed (km/h)"),
              ("halting", "Stopped vehicles (queues and red lights)")]
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                        subplot_titles=[title for _, title in panels])
    for row, (column, title) in enumerate(panels, 1):
        for name, ts in series_by_name.items():
            fig.add_trace(go.Scatter(x=ts["time"] / 3600, y=ts[column], mode="lines", name=name,
                                     legendgroup=name, showlegend=row == 1,
                                     line=dict(width=2, color=SCENARIO_COLORS[name]),
                                     customdata=ts["clock"],
                                     hovertemplate="%{customdata}: %{y:,}<extra>" + name + "</extra>"),
                          row=row, col=1)
    for row in (1, 2, 3):
        _clock_axis(fig, row=row, col=1)
    fig.for_each_annotation(lambda a: a.update(x=0, xanchor="left", font=dict(size=14)))
    _layout(fig, height)
    fig.update_layout(margin=dict(t=80), legend=dict(y=1.08))
    return fig


def group_delay_figure(metrics_by_name, height=360):
    fig = go.Figure()
    labels = [f"{E.GROUP_ICONS[g]} {E.GROUP_LABELS[g]}" for g in E.GROUPS]
    for name, metrics in metrics_by_name.items():
        values = [metrics.get(f"delay_{g}_min") or 0 for g in E.GROUPS]
        fig.add_trace(go.Bar(x=labels, y=values, name=name, marker_color=SCENARIO_COLORS[name],
                             text=[f"{v:.1f}" for v in values], textposition="outside",
                             hovertemplate="%{x}: %{y:.1f} min<extra>" + name + "</extra>"))
    fig.update_layout(barmode="group", bargap=0.3,
                      title=dict(text="Average delay per trip, by group (minutes)", x=0, font=dict(size=15)))
    fig.update_yaxes(title="minutes")
    return _layout(fig, height)


def snapshot_figure(model, positions_by_name, time_s, height=470):
    """Cars at one moment, scenarios side by side."""
    names = list(positions_by_name)
    frames = {n: positions_by_name[n][positions_by_name[n]["time"] == time_s] for n in names}
    fig = make_subplots(rows=1, cols=len(names), horizontal_spacing=0.04,
                        subplot_titles=[f"{n} — {len(frames[n]):,} vehicles" for n in names])
    for col, name in enumerate(names, 1):
        fig.add_trace(_road_traces(model), row=1, col=col)
        cars = frames[name]
        for g in E.GROUPS:
            sub = cars[cars["group"] == g]
            xs, ys = (sub["x"], sub["y"]) if len(sub) else ([None], [None])   # keeps the legend entry
            fig.add_trace(go.Scattergl(x=xs, y=ys, mode="markers", name=E.GROUP_LABELS[g],
                                       legendgroup=g, showlegend=col == 1,
                                       marker=dict(size=5, color=E.COLORS[g]),
                                       hovertemplate=E.GROUP_LABELS[g] + "<extra></extra>"),
                          row=1, col=col)
        suffix = "" if col == 1 else str(col)
        fig.update_xaxes(visible=False, row=1, col=col)
        fig.update_yaxes(visible=False, scaleanchor=f"x{suffix}", scaleratio=1, row=1, col=col)
    return _layout(fig, height)
