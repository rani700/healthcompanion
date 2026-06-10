"""Generate a realistic prescription PNG to test the Gemini vision/OCR path."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def main():
    out = Path(__file__).resolve().parent.parent / "sample_prescription.png"
    W, H = 900, 1100
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    head = _font(34, bold=True)
    sub = _font(20)
    body = _font(26)
    small = _font(20)

    y = 50
    d.text((50, y), "Greenfield Family Clinic", font=head, fill="black")
    y += 46
    d.text((50, y), "Dr. Meera Iyer, MD — Internal Medicine", font=sub, fill="black")
    y += 30
    d.text((50, y), "Reg. No. KA-2287   |   Ph: 080-5550-1144", font=small, fill="black")
    y += 50
    d.line((50, y, W - 50, y), fill="black", width=2)
    y += 30

    d.text((50, y), "Patient: Ravi Kumar        Age: 54        Date: 2026-05-14", font=small, fill="black")
    y += 50
    d.text((50, y), "Rx", font=_font(40, bold=True), fill="black")
    y += 60

    lines = [
        "1. Tab Metformin 500 mg",
        "      1 tablet twice daily, after meals (morning & night)  x 30 days",
        "",
        "2. Tab Amlodipine 5 mg",
        "      1 tablet once daily, in the morning  x 30 days",
        "",
        "3. Tab Atorvastatin 10 mg",
        "      1 tablet at night (after dinner)  x 30 days",
        "",
        "4. Cap Vitamin D3 60000 IU",
        "      1 capsule once weekly (every Sunday)  x 8 weeks",
    ]
    for ln in lines:
        d.text((60, y), ln, font=body, fill="black")
        y += 40

    y += 30
    d.text((60, y), "Advice: Reduce salt intake. Recheck blood sugar in 4 weeks.", font=small, fill="black")
    y += 60
    d.text((W - 300, y), "Dr. Meera Iyer", font=sub, fill="black")
    y += 28
    d.text((W - 300, y), "(Signature)", font=small, fill="black")

    img.save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
