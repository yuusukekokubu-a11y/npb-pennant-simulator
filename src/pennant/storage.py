"""保存の差し替え口(D-083、D-101)。

計算本体の中で、ファイルを読み書きするのはこのモジュールだけ。ほかのモジュールは、ここにある関数や
Storage の形を通して読み書きする(PC とブラウザで、同じ計算本体を使うため)。

  - Storage:保存の読み書きの形(write・read・list)
  - LocalFolderStorage:PC のフォルダ(既定は ~/.npb-pennant-simulator/)
  - MemoryStorage:メモリ(テストとブラウザ。ブラウザでは、画面がダウンロード・ファイル選択で受け渡す)
  - read_text_file:場所を指定して文字のファイルを読む(設定ファイル。D-092)
  - read_package_text:パッケージに同梱した初期データを読む(src/pennant/data/)
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Protocol

DEFAULT_FOLDER = "~/.npb-pennant-simulator"


class StorageError(Exception):
    """保存先の読み書きができなかったとき(理由は日本語)。"""


class Storage(Protocol):
    """保存の読み書きの形(差し替え用)。name はファイルの名前(フォルダの区切りは含めない)。"""

    def write(self, name: str, data: bytes) -> None: ...

    def read(self, name: str) -> bytes: ...

    def list(self) -> list[str]: ...


def _check_name(name: str) -> None:
    if not name or "/" in name or "\\" in name or name in (".", "..") or "\0" in name:
        raise StorageError(f"ファイルの名前に使えない文字があります: {name!r}")


class MemoryStorage:
    """メモリの上に保存する(テストとブラウザ)。"""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def write(self, name: str, data: bytes) -> None:
        _check_name(name)
        self.files[name] = bytes(data)

    def read(self, name: str) -> bytes:
        if name not in self.files:
            raise StorageError(f"{name} が見つかりません")
        return self.files[name]

    def list(self) -> list[str]:
        return sorted(self.files)


class LocalFolderStorage:
    """PC のフォルダに保存する。folder を省略すると ~/.npb-pennant-simulator/(リポジトリの外。D-005)。"""

    def __init__(self, folder: str | Path | None = None) -> None:
        self.folder = Path(folder if folder is not None else DEFAULT_FOLDER).expanduser()

    def write(self, name: str, data: bytes) -> None:
        _check_name(name)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            # 途中で止まっても前のファイルが壊れないよう、別名で書いてから置き換える(同じフォルダの中だけで行う)
            tmp = self.folder / f".{name}.partial"
            tmp.write_bytes(data)
            tmp.replace(self.folder / name)
        except OSError as exc:
            raise StorageError(f"{self.folder} に保存できません({exc.strerror or exc})") from exc

    def read(self, name: str) -> bytes:
        _check_name(name)
        try:
            return (self.folder / name).read_bytes()
        except OSError as exc:
            raise StorageError(f"{self.folder / name} を読めません({exc.strerror or exc})") from exc

    def list(self) -> list[str]:
        if not self.folder.is_dir():
            return []
        return sorted(p.name for p in self.folder.iterdir() if p.is_file() and not p.name.startswith("."))


def read_text_file(path: str | Path) -> str:
    """場所を指定して、文字のファイル(UTF-8)を読む。読めなければ OSError。"""
    return Path(path).read_text(encoding="utf-8")


def read_package_text(*parts: str) -> str:
    """パッケージに同梱した初期データ(src/pennant/data/ など)を読む。"""
    return resources.files("pennant").joinpath(*parts).read_text(encoding="utf-8")
