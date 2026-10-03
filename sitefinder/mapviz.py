"""Stage 5: interactive HTML map of ranked markets."""

import html

import folium
import pandas as pd


def _color(score):
    if score >= 75:
        return "#1a9850"
    if score >= 55:
        return "#91cf60"
    if score >= 40:
        return "#fee08b"
    return "#d73027"


def _text(value):
    return "" if pd.isna(value) else html.escape(str(value))


def _fmt(value, digits=0):
    if pd.isna(value):
        return "-"
    return f"{value:.{digits}f}" if digits else f"{int(value)}"


def make_map(ranked, top_n=50):
    center = [ranked["lat"].mean(), ranked["lng"].mean()]
    fmap = folium.Map(location=center, zoom_start=10, tiles="OpenStreetMap")
    top = folium.FeatureGroup(name=f"Top {top_n}", show=True)
    rest = folium.FeatureGroup(name="ตลาดอื่น ๆ", show=False)

    for r in ranked.itertuples(index=False):
        is_top = r.rank <= top_n
        popup = (
            f"<b>#{r.rank} {_text(r.name)}</b><br>"
            f"score <b>{r.score}</b> ({_text(r.why)})<br>"
            f"⭐ {_fmt(r.rating, 1)} · {_fmt(r.reviews)} รีวิว<br>"
            f"<small>{_text(r.hours_text)}</small><br>"
            f"<a href='{_text(r.maps_url)}' target='_blank'>Google Maps</a>"
        )
        folium.CircleMarker(
            location=[r.lat, r.lng],
            radius=9 if is_top else 4,
            color=_color(r.score),
            fill=True,
            fill_opacity=0.85 if is_top else 0.5,
            weight=1,
            popup=folium.Popup(popup, max_width=320),
            tooltip=f"#{r.rank} {r.name} ({r.score})",
        ).add_to(top if is_top else rest)

    top.add_to(fmap)
    rest.add_to(fmap)
    folium.LayerControl().add_to(fmap)
    return fmap
