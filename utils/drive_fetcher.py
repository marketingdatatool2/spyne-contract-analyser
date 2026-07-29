import re
import io
import requests
import pdfplumber


def extract_file_id(url):
    patterns = [
        r'/file/d/([a-zA-Z0-9_-]+)',
        r'[?&]id=([a-zA-Z0-9_-]+)',
        r'/document/d/([a-zA-Z0-9_-]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def is_google_doc(url):
    return 'docs.google.com/document' in url


def fetch_contract_text(url):
    """Fetch and return plain text from a Google Drive PDF or Google Doc (public links)."""
    if not url or not url.strip():
        raise ValueError("Empty contract URL")

    url = url.strip()

    if is_google_doc(url):
        return _fetch_google_doc(url)

    file_id = extract_file_id(url)
    if not file_id:
        raise ValueError(f"Cannot extract file ID from: {url}")

    return _fetch_pdf(file_id)


def _fetch_google_doc(url):
    file_id = extract_file_id(url)
    if not file_id:
        raise ValueError(f"Cannot extract doc ID from: {url}")
    export_url = f"https://docs.google.com/document/d/{file_id}/export?format=txt"
    resp = requests.get(export_url, timeout=30)
    resp.raise_for_status()
    return resp.text


def _fetch_pdf(file_id):
    session = requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }

    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    resp = session.get(download_url, headers=headers, timeout=60)

    # Google sometimes returns an HTML confirmation page for large/unscanned files
    content_type = resp.headers.get("content-type", "")
    if "text/html" in content_type:
        # Try with confirm param
        confirm_url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t"
        resp = session.get(confirm_url, headers=headers, timeout=60)
        content_type = resp.headers.get("content-type", "")

    if "text/html" in content_type:
        raise ValueError("Could not download PDF — Drive returned an HTML page. Check file permissions.")

    try:
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            pages = []
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text()
                if text:
                    pages.append(f"--- Page {i} ---\n{text}")
            if not pages:
                raise ValueError("PDF has no extractable text (may be scanned image).")
            return "\n\n".join(pages)
    except Exception as e:
        raise ValueError(f"PDF parsing failed: {e}")
