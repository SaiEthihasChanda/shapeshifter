from .archives import ArchiveBackend
from .base import Backend, Cancelled, Context, ConversionError, RiskyFile
from .data import DataBackend
from .external import LibreOfficeBackend, PandocBackend
from .images import ImageBackend
from .media import MediaBackend
from .mupdf import MuPDFBackend
from ..native.backend import NativeBackend  # noqa: E402
from .family import FamilyBackend  # noqa: E402
from .ocr import OcrBackend  # noqa: E402
from .picturedoc import PictureDocBackend  # noqa: E402
from .tables import TableBackend  # noqa: E402
from .slides import DocReadBackend, SlidesBackend  # noqa: E402
from ..custom import CustomFormatBackend  # noqa: E402

# Each backend contributes edges to the conversion graph (see planner.py). When several
# can do the same step, the planner picks the cheapest; if that one raises Unsupported
# the next cheapest is tried.
ALL_BACKENDS: list[Backend] = [
    NativeBackend(),
    ImageBackend(),
    MuPDFBackend(),
    MediaBackend(),
    DataBackend(),
    ArchiveBackend(),
    PandocBackend(),
    LibreOfficeBackend(),
    CustomFormatBackend(),
    FamilyBackend(),
    OcrBackend(),
    PictureDocBackend(),
    TableBackend(),
    SlidesBackend(),
    DocReadBackend(),
]

__all__ = ["ALL_BACKENDS", "Backend", "Cancelled", "Context", "ConversionError", "RiskyFile"]
