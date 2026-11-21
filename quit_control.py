"""Layout and hit testing for the in-game quit button."""

from dataclasses import dataclass

_WIDTH = 112
_HEIGHT = 44


@dataclass(frozen=True)
class QuitButton:
    rect: tuple[int, int, int, int]

    def visible(self, game_running: bool) -> bool:
        return bool(game_running)

    def hit(self, x: float, y: float) -> bool:
        """Test the rectangle only; the caller is responsible for visibility."""
        left, top, width, height = self.rect
        return left <= x < left + width and top <= y < top + height


def top_right(canvas_w: int, margin: int) -> QuitButton:
    """Place a standard quit button inside the top and right margins."""
    return QuitButton((canvas_w - margin - _WIDTH, margin, _WIDTH, _HEIGHT))
