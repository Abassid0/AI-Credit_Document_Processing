"""
run_local_test.py — Runs the real extraction + validation pipeline against
a folder of document images, without needing Telegram running at all.
Useful for iterating fast before you record the demo video.

Requires a real ANTHROPIC_API_KEY (this makes live API calls — 4 calls per
run, one per document). It does NOT need TELEGRAM_BOT_TOKEN.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python tools/run_local_test.py test_documents/clean
    python tools/run_local_test.py test_documents/flagged
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # allow running from tools/

from extraction import extract_document
from validation import validate_application, ExtractedDocument
from bot import _format_comparison_table  # reuses the exact formatter the bot uses

# Maps filename prefix -> doc_type, matching config.REQUIRED_DOCUMENTS_ORDER
FILENAME_TO_DOC_TYPE = {
    "0_passport_photo":  "passport_photo",
    "1_government_id":   "government_id",
    "2_bank_statement":  "bank_statement",
    "3_proof_of_income": "proof_of_income",
    "4_proof_of_address":"proof_of_address",
}


def run(folder: str) -> None:
    folder_path = Path(folder)
    if not folder_path.is_dir():
        print(f"Folder not found: {folder}")
        sys.exit(1)

    documents: list[ExtractedDocument] = []
    start = time.monotonic()

    for filename, doc_type in FILENAME_TO_DOC_TYPE.items():
        matches = list(folder_path.glob(f"{filename}.*"))
        if not matches:
            print(f"  (skipping {doc_type} — no file matching {filename}.* found)")
            continue
        file_path = matches[0]
        print(f"Extracting {doc_type} from {file_path.name}...")
        image_bytes = file_path.read_bytes()
        extracted = extract_document(image_bytes, filename=file_path.name, doc_type=doc_type)
        documents.append(extracted)

    if not documents:
        print("No documents processed — nothing to validate.")
        return

    result = validate_application(documents)
    elapsed = time.monotonic() - start

    print()
    print("=" * 60)
    print(f"Completeness: {result.completeness_pct}%")
    print(f"Ready for underwriting review: {result.ready_for_underwriting}")
    print(f"Processed in {elapsed:.1f} seconds")
    print()
    if result.flags:
        print("Flags:")
        for f in result.flags:
            print(f"  [{f.severity.upper()}] {f.code}: {f.message}")
    else:
        print("No issues found.")
    print()
    print(_format_comparison_table(result.field_comparison))
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python tools/run_local_test.py <folder-of-test-documents>")
        sys.exit(1)
    run(sys.argv[1])
