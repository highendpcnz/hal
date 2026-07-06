"""Chess games for the HAL frontend — one game per browser session.

Wraps chess_engine with persistence (data/chess/<session>.json survives
restarts), spoken/typed move parsing, and HAL's narration lines. All methods
are synchronous; engine searches are CPU-bound, so callers run advance()/
new_game() in a worker thread (asyncio.to_thread) like TTS synthesis.

The film reserved "I'm sorry, Frank — I think you missed it" for delivering
mate; HAL does the same to Dave.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import chess_engine as ce

CHESS_DEPTH = int(os.environ.get("HAL_CHESS_DEPTH", "3"))
CHESS_TIME = float(os.environ.get("HAL_CHESS_TIME", "4"))

# Session ids become file names — same validation rule as main.py's.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")

_PIECE_WORDS = {
    "pawn": "P", "knight": "N", "horse": "N", "bishop": "B",
    "rook": "R", "castle": "R", "queen": "Q", "king": "K",
}
_PIECE_NAMES = {"P": "pawn", "N": "knight", "B": "bishop", "R": "rook", "Q": "queen", "K": "king"}
_NUM_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8",
}
_SQUARE_RE = re.compile(r"\b([a-h])\s*([1-8])\b")
_PROMO_RE = re.compile(r"promot\w*\s+(?:it\s+)?(?:to\s+)?(?:a\s+)?(queen|rook|bishop|knight)")
_TAKES_RE = re.compile(r"\b(?:takes?|captures?)\b")


def _normalize_spoken(text: str) -> str:
    text = text.lower()
    # "e four" -> "e4"; whisper writes squares both ways
    for word, digit in _NUM_WORDS.items():
        text = re.sub(rf"\b([a-h])\s+{word}\b", rf"\g<1>{digit}", text)
    return re.sub(r"[^a-z0-9\s-]", " ", text)


class ChessManager:
    def __init__(self, data_dir: Path):
        self.games_dir = data_dir / "chess"
        self.games_dir.mkdir(parents=True, exist_ok=True)

    def _file(self, session_id: str) -> Path:
        if not _SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("invalid session id")
        return self.games_dir / f"{session_id}.json"

    def load(self, session_id: str) -> Optional[dict]:
        try:
            game = json.loads(self._file(session_id).read_text())
        except (OSError, json.JSONDecodeError, ValueError):
            return None
        return game if isinstance(game, dict) and "fen" in game else None

    def _save(self, session_id: str, game: dict) -> None:
        f = self._file(session_id)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(game))
        tmp.replace(f)

    # -- lifecycle -----------------------------------------------------------

    def new_game(self, session_id: str, dave_color: str = "w") -> tuple[dict, str]:
        """Fresh game; if HAL has white he moves immediately (engine search —
        run this in a worker thread). Returns (game, spoken line)."""
        game = {
            "fen": ce.START_FEN,
            "dave_color": "w" if dave_color != "b" else "b",
            "status": "active",
            "outcome": None,
            "moves": [],          # [{"uci", "san"}]
            "keys": {},           # position key -> occurrences (threefold)
            "last_move": None,
            "updated_at": time.time(),
        }
        self._bump_key(game, ce.Board.start())
        if game["dave_color"] == "b":
            spoken = self._hal_move(game)
            line = f"Very well, Dave. I'll take white. {spoken}"
        else:
            line = "Very well, Dave. The board is set, you have white. Your move."
        self._save(session_id, game)
        return game, line

    def drop(self, session_id: str) -> None:
        """Forget the session's game (browser session reset)."""
        try:
            self._file(session_id).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    def resign(self, session_id: str) -> Optional[str]:
        game = self.load(session_id)
        if game is None or game["status"] != "active":
            return None
        game["status"] = "finished"
        game["outcome"] = "hal_wins"
        game["updated_at"] = time.time()
        self._save(session_id, game)
        return "I accept, Dave. A good game. Another whenever you're ready."

    # -- move handling ---------------------------------------------------------

    def resolve(self, game: dict, text: str, typed: bool) -> Optional[tuple]:
        """Interpret an utterance as a move against the current position.

        Returns None when the text isn't chess (falls through to the brain),
        ("move", mv), ("ambiguous", moves), or ("illegal", description) when
        it clearly was a move attempt that can't be played.
        """
        board = ce.Board.from_fen(game["fen"])
        legal = board.legal_moves()

        stripped = text.strip().rstrip(".!?")
        # Exact notation first: UCI ("e2e4") or SAN ("Nf3", "O-O", "e8=Q").
        if re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", stripped.lower()):
            for mv in legal:
                if ce.move_uci(mv) == stripped.lower():
                    return ("move", mv)
        san_text = stripped.replace("0-0-0", "O-O-O").replace("0-0", "O-O")
        for mv in legal:
            if ce.san(board, mv).rstrip("+#") == san_text.rstrip("+#"):
                return ("move", mv)
        if typed:
            return None  # typed input is exact or it isn't chess

        spoken = _normalize_spoken(text)
        if "castle" in spoken or "castles" in spoken:
            castles = [
                mv for mv in legal
                if board.sq[mv[0]].upper() == "K" and abs(mv[1] - mv[0]) == 2
            ]
            if re.search(r"\b(king\s*side|kingside|short)\b", spoken):
                castles = [mv for mv in castles if mv[1] > mv[0]]
            elif re.search(r"\b(queen\s*side|queenside|long)\b", spoken):
                castles = [mv for mv in castles if mv[1] < mv[0]]
            if len(castles) == 1:
                return ("move", castles[0])
            if castles:
                return ("ambiguous", castles)
            return ("illegal", "castling")

        squares = _SQUARE_RE.findall(spoken)
        piece = next((p for w, p in _PIECE_WORDS.items() if re.search(rf"\b{w}s?\b", spoken)), None)
        if not squares:
            return None  # no square named — not a move
        promo_match = _PROMO_RE.search(spoken)
        promo = promo_match.group(1)[0].replace("k", "n") if promo_match else ""
        wants_capture = _TAKES_RE.search(spoken) is not None

        if len(squares) >= 2:
            frm = ce.parse_square(squares[0][0] + squares[0][1])
            to = ce.parse_square(squares[1][0] + squares[1][1])
            matches = [mv for mv in legal if mv[0] == frm and mv[1] == to]
        else:
            to = ce.parse_square(squares[-1][0] + squares[-1][1])
            matches = [mv for mv in legal if mv[1] == to]
            if piece:
                matches = [mv for mv in matches if board.sq[mv[0]].upper() == piece]
            else:
                # A bare square is a pawn move by convention — but only when
                # the utterance IS the square ("e4", "HAL, e4"), otherwise
                # ordinary conversation that happens to contain a square-like
                # token ("gate B4") would hijack the turn mid-game.
                bare = re.fullmatch(
                    r"(?:hey\s+|ok\s+|okay\s+)?(?:hal\s+)?(?:to\s+)?[a-h][1-8]",
                    spoken.strip(),
                )
                if bare is None and not wants_capture:
                    return None  # not clearly a move — leave it to the brain
                pawn_matches = [mv for mv in matches if board.sq[mv[0]].upper() == "P"]
                if bare and pawn_matches:
                    matches = pawn_matches
            if wants_capture:
                capture_matches = [mv for mv in matches if board.is_capture(mv)]
                if capture_matches:
                    matches = capture_matches
        if promo:
            promo_matches = [mv for mv in matches if mv[2] == promo]
            matches = promo_matches or matches
        # Collapse promotion variants: same from/to differing only in piece
        # defaults to the queen.
        if len(matches) == 4 and len({(m[0], m[1]) for m in matches}) == 1:
            matches = [mv for mv in matches if mv[2] == "q"]

        if len(matches) == 1:
            return ("move", matches[0])
        if len(matches) > 1:
            return ("ambiguous", matches)
        if piece or len(squares) >= 2 or wants_capture:
            wanted = _PIECE_NAMES.get(piece, "that")
            return ("illegal", f"{wanted} to {squares[-1][0]}{squares[-1][1]}")
        return None

    def advance(self, session_id: str, game: dict, move: tuple) -> str:
        """Apply Dave's move, let HAL reply, and return HAL's spoken line.
        Blocking (engine search) — run in a worker thread."""
        board = ce.Board.from_fen(game["fen"])
        self._push(game, board, move)
        board = ce.Board.from_fen(game["fen"])

        over = self._finish_if_over(game, board, dave_just_moved=True)
        if over is not None:
            self._save(session_id, game)
            return over

        spoken = self._hal_move(game)
        board = ce.Board.from_fen(game["fen"])
        over = self._finish_if_over(game, board, dave_just_moved=False)
        if over is not None:
            self._save(session_id, game)
            last_san = game["moves"][-1]["san"]
            if last_san.endswith("#"):
                return f"I'm sorry, Dave. I think you missed it. {spoken} Mate."
            return f"{spoken} {over}".strip()
        self._save(session_id, game)
        return spoken

    # -- internals ---------------------------------------------------------

    def _bump_key(self, game: dict, board: ce.Board) -> int:
        key = board.position_key()
        game["keys"][key] = game["keys"].get(key, 0) + 1
        return game["keys"][key]

    def _push(self, game: dict, board: ce.Board, move: tuple) -> None:
        san_text = ce.san(board, move)
        child = board.apply(move)
        game["fen"] = child.to_fen()
        game["moves"].append({"uci": ce.move_uci(move), "san": san_text})
        game["last_move"] = ce.move_uci(move)
        game["updated_at"] = time.time()
        self._bump_key(game, child)

    def _hal_move(self, game: dict) -> str:
        board = ce.Board.from_fen(game["fen"])
        move = ce.best_move(board, depth=CHESS_DEPTH, time_budget=CHESS_TIME)
        spoken = self._spoken_move(board, move)
        self._push(game, board, move)
        return spoken

    @staticmethod
    def _spoken_move(board: ce.Board, move: tuple) -> str:
        frm, to, promo = move
        piece = board.sq[frm].upper()
        if piece == "K" and abs(to - frm) == 2:
            side = "kingside" if to > frm else "queenside"
            text = f"I'll castle {side}."
        else:
            name = _PIECE_NAMES[piece].capitalize()
            verb = "takes on" if board.is_capture(move) else "to"
            text = f"{name} {verb} {ce.square_name(to)}."
            if promo:
                text = f"Pawn to {ce.square_name(to)}, promoting to a {_PIECE_NAMES[promo.upper()]}."
        child = board.apply(move)
        if child.in_check() and child.legal_moves():
            text += " Check."
        return text

    def _finish_if_over(self, game: dict, board: ce.Board, dave_just_moved: bool) -> Optional[str]:
        """Set final status and return the spoken verdict, or None."""
        if not board.legal_moves():
            game["status"] = "finished"
            if board.in_check():
                game["outcome"] = "dave_wins" if dave_just_moved else "hal_wins"
                if dave_just_moved:
                    return "Checkmate, Dave. Well played. I never saw it coming."
                return ""  # game over; advance() composes HAL's mate line
            game["outcome"] = "draw"
            return "Stalemate, Dave. An honorable draw."
        if board.halfmove >= 100:
            game["status"] = "finished"
            game["outcome"] = "draw"
            return "Fifty moves without progress, Dave. I declare a draw."
        if max(game["keys"].values(), default=0) >= 3:
            game["status"] = "finished"
            game["outcome"] = "draw"
            return "Threefold repetition, Dave. A draw, then."
        return None


manager: ChessManager | None = None


def init(data_dir: Path) -> None:
    global manager
    manager = ChessManager(data_dir)
