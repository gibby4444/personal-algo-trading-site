"""Tiny SVG charts. Numbers only, so the markup can be embedded as-is."""

from __future__ import annotations


def line_svg(points: list[tuple[float, float]], *, width: int = 720, height: int = 200, stroke: str = "#e0a045") -> str:
    if len(points) < 2:
        return (
            f'<svg viewBox="0 0 {width} {height}" class="chart" role="img">'
            f'<text x="16" y="32" fill="#a3aa96">Not enough points</text></svg>'
        )
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if max_x == min_x:
        max_x += 1
    if max_y == min_y:
        max_y += 1
    pad = 12

    def sx(x: float) -> float:
        return pad + (x - min_x) / (max_x - min_x) * (width - pad * 2)

    def sy(y: float) -> float:
        return height - pad - (y - min_y) / (max_y - min_y) * (height - pad * 2)

    cmds = " ".join(f"{sx(x):.2f},{sy(y):.2f}" for x, y in points)
    last = points[-1][1]
    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart" role="img">'
        f'<polyline fill="none" stroke="{stroke}" stroke-width="2" points="{cmds}" />'
        f'<text x="{width - 120}" y="24" fill="#ebe6d6" font-size="13">{last:,.0f}</text>'
        f"</svg>"
    )


def equity_svg(points: list[dict]) -> str:
    series = [(index, float(point["equity"])) for index, point in enumerate(points)]
    return line_svg(series, stroke="#e0a045")


def drawdown_svg(points: list[dict]) -> str:
    series = [(index, float(point["drawdown"]) * 100) for index, point in enumerate(points)]
    return line_svg(series, height=120, stroke="#e07a68")
