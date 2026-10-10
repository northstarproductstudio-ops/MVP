"""Supabase Storage wrapper. All Parquet and uploaded files go through ``Store``.

Rules (Deployment-Readiness Spec, Storage layer):
* keys start with ``workspaces/<company_id>/`` or ``runs/<company_id>/``
* writes are write-once (``x-upsert: false``); a second write to a key raises
* ``put_parquet`` returns the sha256 of the bytes (the table fingerprint)
* ``get_parquet`` downloads to TMP_DIR, cached by key, and verifies the hash when given
"""

from __future__ import annotations

import hashlib
import io
import re
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from core.logging import get_logger

log = get_logger(__name__)

_KEY_RE = re.compile(r"^(workspaces|runs|uploads)/[0-9a-f-]{36}/[A-Za-z0-9_./\- ]+$")
RESUMABLE_THRESHOLD = 50 * 1024 * 1024


class StorageError(RuntimeError):
    pass


class KeyExistsError(StorageError):
    pass


class HashMismatchError(StorageError):
    pass


def snapshot_key(company_id: str, session_id: str, snapshot_id: str, table: str) -> str:
    return f"workspaces/{company_id}/{session_id}/{snapshot_id}/{table}.parquet"


def run_key(company_id: str, run_id: str, name: str) -> str:
    return f"runs/{company_id}/{run_id}/{name}.parquet"


def upload_key(company_id: str, session_id: str, sha256: str, file_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.\- ]", "_", file_name)[:120]
    return f"uploads/{company_id}/{session_id}/{sha256[:16]}-{safe}"


def check_key(key: str, company_id: str | None = None) -> None:
    if not _KEY_RE.match(key) or ".." in key:
        raise StorageError(f"invalid storage key: {key!r}")
    if company_id and key.split("/")[1] != str(company_id):
        raise StorageError("storage key does not belong to this company")


def df_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow")
    return buf.getvalue()


class Store:
    def __init__(self, supabase_url: str, service_key: str, bucket: str, tmp_dir: Path,
                 timeout: float = 60.0, retries: int = 3):
        self._base = supabase_url.rstrip("/") + "/storage/v1"
        self._bucket = bucket
        self._tmp = Path(tmp_dir)
        self._retries = retries
        self._client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {service_key}", "apikey": service_key},
        )

    # -- low level ---------------------------------------------------------------
    def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._client.request(method, url, **kw)
            except httpx.TransportError as exc:
                last = exc
            else:
                if resp.status_code < 500:
                    return resp
                last = StorageError(f"storage {resp.status_code}")
            time.sleep(min(2**attempt, 8))
        raise StorageError(f"storage request failed after retries: {type(last).__name__}")

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        check_key(key)
        if len(data) > RESUMABLE_THRESHOLD:
            return self._put_resumable(key, data, content_type)
        url = f"{self._base}/object/{self._bucket}/{key}"
        resp = self._request("POST", url, content=data,
                             headers={"Content-Type": content_type, "x-upsert": "false"})
        if resp.status_code in (400, 409) and "exist" in resp.text.lower():
            raise KeyExistsError(f"write-once violation: {key}")
        if resp.status_code >= 300:
            raise StorageError(f"upload failed ({resp.status_code}) for {key}")
        return hashlib.sha256(data).hexdigest()

    def put_stream(self, key: str, chunks: Iterator[bytes], content_type: str = "application/octet-stream") -> tuple[str, int]:
        """Stream an upload in chunks via a temp file under TMP_DIR (never fully in memory)."""
        self._tmp.mkdir(parents=True, exist_ok=True)
        tmp = self._tmp / f"up-{uuid.uuid4().hex}"
        h = hashlib.sha256()
        size = 0
        try:
            with open(tmp, "wb") as fh:
                for chunk in chunks:
                    h.update(chunk)
                    size += len(chunk)
                    fh.write(chunk)
            url = f"{self._base}/object/{self._bucket}/{key}"
            with open(tmp, "rb") as fh:
                resp = self._request("POST", url, content=fh,
                                     headers={"Content-Type": content_type, "x-upsert": "false"})
            if resp.status_code in (400, 409) and "exist" in resp.text.lower():
                raise KeyExistsError(f"write-once violation: {key}")
            if resp.status_code >= 300:
                raise StorageError(f"upload failed ({resp.status_code}) for {key}")
        finally:
            tmp.unlink(missing_ok=True)
        return h.hexdigest(), size

    def _put_resumable(self, key: str, data: bytes, content_type: str) -> str:
        # TUS resumable upload (Supabase): create, then PATCH in 6 MB chunks.
        import base64

        meta = ",".join(
            f"{k} {base64.b64encode(v.encode()).decode()}"
            for k, v in {"bucketName": self._bucket, "objectName": key, "contentType": content_type}.items()
        )
        create = self._request("POST", f"{self._base}/upload/resumable", headers={
            "Tus-Resumable": "1.0.0", "Upload-Length": str(len(data)), "Upload-Metadata": meta,
            "x-upsert": "false"})
        if create.status_code == 409:
            raise KeyExistsError(f"write-once violation: {key}")
        if create.status_code >= 300:
            raise StorageError(f"resumable create failed ({create.status_code})")
        loc = create.headers["Location"]
        chunk = 6 * 1024 * 1024
        for off in range(0, len(data), chunk):
            r = self._request("PATCH", loc, content=data[off:off + chunk], headers={
                "Tus-Resumable": "1.0.0", "Upload-Offset": str(off),
                "Content-Type": "application/offset+octet-stream"})
            if r.status_code >= 300:
                raise StorageError(f"resumable chunk failed ({r.status_code})")
        return hashlib.sha256(data).hexdigest()

    def get_bytes(self, key: str) -> bytes:
        check_key(key)
        resp = self._request("GET", f"{self._base}/object/authenticated/{self._bucket}/{key}")
        if resp.status_code == 404 or resp.status_code == 400:
            raise StorageError(f"not found: {key}")
        if resp.status_code >= 300:
            raise StorageError(f"download failed ({resp.status_code})")
        return resp.content

    def download_to(self, key: str) -> Path:
        dest = self._tmp / "cache" / key
        if dest.exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + f".{uuid.uuid4().hex}.part")
        tmp.write_bytes(self.get_bytes(key))
        tmp.replace(dest)
        return dest

    # -- interface from the spec ------------------------------------------------
    def put_parquet(self, key: str, df: pd.DataFrame) -> str:
        return self.put_bytes(key, df_to_parquet_bytes(df), "application/vnd.apache.parquet")

    def get_parquet(self, key: str, expected_sha256: str | None = None) -> Path:
        path = self.download_to(key)
        if expected_sha256:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected_sha256:
                path.unlink(missing_ok=True)
                raise HashMismatchError(f"hash mismatch for {key}")
        return path

    def read_parquet(self, key: str, expected_sha256: str | None = None) -> pd.DataFrame:
        return pd.read_parquet(self.get_parquet(key, expected_sha256))

    def exists(self, key: str) -> bool:
        check_key(key)
        resp = self._request("HEAD", f"{self._base}/object/authenticated/{self._bucket}/{key}")
        return resp.status_code == 200

    def delete_prefix(self, prefix: str) -> int:
        """Used only by the expiry job."""
        check_key(prefix.rstrip("/") + "/x")
        listing = self._request("POST", f"{self._base}/object/list/{self._bucket}",
                                json={"prefix": prefix, "limit": 1000})
        names = [f"{prefix.rstrip('/')}/{o['name']}" for o in listing.json() if o.get("id")]
        if names:
            self._request("DELETE", f"{self._base}/object/{self._bucket}", json={"prefixes": names})
        return len(names)

    def signed_url(self, key: str, expires_in: int = 300) -> str:
        check_key(key)
        resp = self._request("POST", f"{self._base}/object/sign/{self._bucket}/{key}",
                             json={"expiresIn": expires_in})
        if resp.status_code >= 300:
            raise StorageError("could not sign url")
        return self._base + resp.json()["signedURL"]

    def ping(self) -> bool:
        try:
            resp = self._request("GET", f"{self._base}/bucket/{self._bucket}")
            return resp.status_code == 200
        except StorageError:
            return False


def clean_tmp(tmp_dir: Path) -> None:
    import shutil

    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
