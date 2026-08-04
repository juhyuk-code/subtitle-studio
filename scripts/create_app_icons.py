from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "packaging" / "generated"
SIZE = 1024


def build_icon() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), "#f5f2ea")
    draw = ImageDraw.Draw(image)
    margin = 72
    draw.rounded_rectangle(
        (margin, margin, SIZE - margin, SIZE - margin),
        radius=190,
        fill="#f5f2ea",
        outline="#d7d2c7",
        width=8,
    )
    center = SIZE // 2
    radius = 325
    draw.ellipse(
        (center - radius, center - radius, center + radius, center + radius),
        fill="#242521",
    )
    heights = (128, 252, 390, 520, 390, 252, 128)
    bar_width = 54
    gap = 34
    total_width = len(heights) * bar_width + (len(heights) - 1) * gap
    left = center - total_width // 2
    for index, height in enumerate(heights):
        x = left + index * (bar_width + gap)
        draw.rounded_rectangle(
            (x, center - height // 2, x + bar_width, center + height // 2),
            radius=bar_width // 2,
            fill="#de623f",
        )
    return image


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    icon = build_icon()
    icon.save(OUTPUT / "SubtitleStudio.png", format="PNG")
    icon.save(
        OUTPUT / "SubtitleStudio.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    icon.save(OUTPUT / "SubtitleStudio.icns", format="ICNS")


if __name__ == "__main__":
    main()
