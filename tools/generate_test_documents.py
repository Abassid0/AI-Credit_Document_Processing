"""
generate_test_documents.py — Creates synthetic document images for testing
the credit application bot, without ever using real applicant data.

Every generated image is stamped "SPECIMEN — SYNTHETIC TEST DOCUMENT" and
uses obviously fictitious names, numbers, and institutions. These are
deliberately NOT close replicas of real Nigerian ID/bank document formats.

Generates two sets:
  test_documents/clean/    — all 5 docs consistent, should score ~100%
  test_documents/flagged/  — same persona, deliberate mismatches to demo
                             the consistency checks catching them.

Run: python tools/generate_test_documents.py
"""
import os
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ── Font resolution (Windows + Linux/Mac) ───────────────────────────────────
def _find_font(bold: bool = False) -> str:
    candidates_bold = [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\calibrib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    candidates_regular = [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in (candidates_bold if bold else candidates_regular):
        if os.path.exists(path):
            return path
    return ""   # falls back to PIL default bitmap font


def _font(bold: bool, size: int) -> ImageFont.ImageFont:
    path = _find_font(bold)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


# ── Shared helpers ───────────────────────────────────────────────────────────
OUT_DIR = Path(__file__).resolve().parent.parent / "test_documents"
WIDTH, HEIGHT = 1000, 700
MARGIN = 50


def _new_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (WIDTH, HEIGHT), "white")
    return img, ImageDraw.Draw(img)


def _apply_watermark(img: Image.Image) -> Image.Image:
    text = "SPECIMEN — SYNTHETIC TEST DOCUMENT — NOT REAL"
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.text((WIDTH // 2, HEIGHT // 2), text, font=_font(True, 22),
               fill=(200, 30, 30, 80), anchor="mm")
    rotated = overlay.rotate(25, expand=False, resample=Image.BICUBIC)
    img = img.convert("RGBA")
    img.alpha_composite(rotated)
    return img.convert("RGB")


def _header(draw: ImageDraw.ImageDraw, title: str, subtitle: str) -> None:
    draw.rectangle([0, 0, WIDTH, 95], fill=(20, 30, 70))
    draw.text((MARGIN, 18), title, font=_font(True, 30), fill="white")
    draw.text((MARGIN, 62), subtitle, font=_font(False, 16), fill=(200, 200, 220))


def _field(draw: ImageDraw.ImageDraw, y: int, label: str, value: str) -> None:
    draw.text((MARGIN, y), label + ":", font=_font(True, 17), fill=(80, 80, 80))
    draw.text((MARGIN, y + 28), value, font=_font(False, 22), fill=(10, 10, 10))


# ── Document generators ──────────────────────────────────────────────────────

def make_passport_photo(path: str, full_name: str) -> None:
    """Generates a plain passport-photo placeholder — face oval + label."""
    img = Image.new("RGB", (600, 700), (240, 240, 245))
    draw = ImageDraw.Draw(img)

    # Background
    draw.rectangle([0, 0, 600, 700], fill=(220, 230, 240))

    # Simulated face oval
    draw.ellipse([150, 80, 450, 460], fill=(245, 220, 195), outline=(180, 150, 120), width=3)

    # Simulated eyes
    draw.ellipse([220, 200, 260, 230], fill=(60, 40, 20))
    draw.ellipse([340, 200, 380, 230], fill=(60, 40, 20))

    # Simulated smile
    draw.arc([240, 290, 360, 360], start=10, end=170, fill=(140, 80, 60), width=4)

    # Name label at bottom
    draw.rectangle([0, 560, 600, 620], fill=(20, 30, 70))
    draw.text((300, 590), full_name, font=_font(True, 22), fill="white", anchor="mm")

    # Watermark
    overlay = Image.new("RGBA", (600, 700), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.text((300, 350), "SPECIMEN", font=_font(True, 48), fill=(200, 30, 30, 70), anchor="mm")
    img = img.convert("RGBA")
    img.alpha_composite(overlay)
    img = img.convert("RGB")
    img.save(path)
    print(f"  ✓ {Path(path).name}")


def make_government_id(path: str, full_name: str, dob: str,
                        id_number: str, nin: str, address: str) -> None:
    img, draw = _new_canvas()
    _header(draw, "DEMO NATIONAL ID SPECIMEN", "Fictitious identity document — testing purposes only")
    y = 130
    for label, value in [
        ("Full Name", full_name),
        ("Date of Birth", dob),
        ("ID Number", id_number),
        ("NIN", nin),
        ("Address", address),
    ]:
        _field(draw, y, label, value)
        y += 85
    img = _apply_watermark(img)
    img.save(path)
    print(f"  ✓ {Path(path).name}")


def make_bank_statement(path: str, full_name: str, account_number: str,
                         bvn: str, statement_date: str, address: str) -> None:
    img, draw = _new_canvas()
    _header(draw, "SAMPLE BANK PLC — ACCOUNT STATEMENT",
            "Fictitious bank — testing purposes only")
    y = 130
    for label, value in [
        ("Account Name", full_name),
        ("Account Number", account_number),
        ("BVN", bvn),
        ("Registered Address", address),
        ("Statement Period End", statement_date),
    ]:
        _field(draw, y, label, value)
        y += 85
    img = _apply_watermark(img)
    img.save(path)
    print(f"  ✓ {Path(path).name}")


def make_proof_of_income(path: str, full_name: str, employer: str,
                          income: str, dob: str) -> None:
    img, draw = _new_canvas()
    _header(draw, "DEMO PAYSLIP", "Fictitious employer — testing purposes only")
    y = 130
    for label, value in [
        ("Employee Name", full_name),
        ("Date of Birth", dob),
        ("Employer / Business", employer),
        ("Net Income (Monthly)", income),
    ]:
        _field(draw, y, label, value)
        y += 85
    img = _apply_watermark(img)
    img.save(path)
    print(f"  ✓ {Path(path).name}")


def make_proof_of_address(path: str, full_name: str,
                           address: str, bill_date: str) -> None:
    img, draw = _new_canvas()
    _header(draw, "DEMO UTILITY BILL",
            "Fictitious utility provider — testing purposes only")
    y = 130
    for label, value in [
        ("Customer Name", full_name),
        ("Service Address", address),
        ("Bill Date", bill_date),
    ]:
        _field(draw, y, label, value)
        y += 85
    img = _apply_watermark(img)
    img.save(path)
    print(f"  ✓ {Path(path).name}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    clean_dir   = OUT_DIR / "clean"
    flagged_dir = OUT_DIR / "flagged"
    clean_dir.mkdir(parents=True, exist_ok=True)
    flagged_dir.mkdir(parents=True, exist_ok=True)

    name    = "Chidinma Test Okoye"
    dob     = "1994-03-12"
    address = "14 Demo Avenue, Ikeja, Lagos"

    # ── CLEAN SET ────────────────────────────────────────────────────────────
    print("\nGenerating clean set...")
    make_passport_photo(str(clean_dir / "0_passport_photo.png"), name)
    make_government_id(
        str(clean_dir / "1_government_id.png"),
        full_name=name, dob=dob, id_number="TEST-000111",
        nin="00000000001", address=address,
    )
    make_bank_statement(
        str(clean_dir / "2_bank_statement.png"),
        full_name=name, account_number="0000111122", bvn="00000000002",
        statement_date="2026-08-10", address=address,
    )
    make_proof_of_income(
        str(clean_dir / "3_proof_of_income.png"),
        full_name=name, employer="Demo Textiles Ltd (fictitious)",
        income="NGN 450,000", dob=dob,
    )
    make_proof_of_address(
        str(clean_dir / "4_proof_of_address.png"),
        full_name=name, address=address, bill_date="2026-08-05",
    )

    # ── FLAGGED SET ──────────────────────────────────────────────────────────
    print("\nGenerating flagged set...")
    make_passport_photo(str(flagged_dir / "0_passport_photo.png"), name)
    make_government_id(
        str(flagged_dir / "1_government_id.png"),
        full_name=name, dob=dob, id_number="TEST-000111",
        nin="00000000001", address=address,
    )
    make_bank_statement(
        str(flagged_dir / "2_bank_statement.png"),
        full_name=name, account_number="0000111122", bvn="00000000002",
        statement_date="2026-01-15",   # stale — > 90 days before Aug 2026
        address=address,
    )
    make_proof_of_income(
        str(flagged_dir / "3_proof_of_income.png"),
        full_name=name, employer="Demo Textiles Ltd (fictitious)",
        income="NGN 450,000",
        dob="1985-11-02",             # WRONG — deliberate DOB mismatch
    )
    make_proof_of_address(
        str(flagged_dir / "4_proof_of_address.png"),
        full_name="C. Test Okoye",    # WRONG — deliberate name mismatch
        address="9 Different Street, Surulere, Lagos",  # WRONG — address mismatch
        bill_date="2026-08-05",
    )

    print(f"\nClean set   → {clean_dir}")
    print(f"Flagged set → {flagged_dir}")
    print("\nExpected clean:   ~100% complete, no blockers.")
    print("Expected flagged: DOB mismatch blocker, name/address warnings, stale-statement warning.")


if __name__ == "__main__":
    main()
