"""Clean-room chess engine for the HAL chess panel.

Move generation (0x88 board), FEN, SAN, and a small alpha-beta search with
quiescence. Written from scratch for this repo — no code from python-chess
or sunfish (both GPL) is imported or adapted, which keeps the repo MIT.

Correctness is pinned by perft counts in tests/run.py — the standard node
counts catch castling/en-passant/promotion bugs that eyeballing never will.
Strength is club-level by design: HAL should be worth playing, not a cloud
engine. Depth/time come from HAL_CHESS_DEPTH / HAL_CHESS_TIME.

Boards are immutable-in-practice: apply() returns a copy. That costs some
speed but removes the whole unmake-bug class; the search compensates by
validating legality lazily (apply the pseudo-legal move, skip if the mover's
king is attacked) so each explored move is copied exactly once.
"""
from __future__ import annotations

import time

# Squares are rank*16 + file (0x88): off-board is (sq & 0x88) != 0.
# White pieces are uppercase, black lowercase, empty squares "".
_FILES = "abcdefgh"

KNIGHT_OFFSETS = (33, 31, 18, 14, -14, -18, -31, -33)
DIAG_DIRS = (17, 15, -15, -17)
ORTH_DIRS = (16, -16, 1, -1)
KING_DIRS = DIAG_DIRS + ORTH_DIRS

PIECE_VALUES = {"P": 100, "N": 320, "B": 330, "R": 500, "Q": 900, "K": 0}
MATE = 100_000

# Castling bookkeeping: touching any of these squares clears rights.
_CASTLE_CLEAR = {0x04: "KQ", 0x07: "K", 0x00: "Q", 0x74: "kq", 0x77: "k", 0x70: "q"}


def square(file: int, rank: int) -> int:
    return rank * 16 + file


def square_name(sq: int) -> str:
    return _FILES[sq & 7] + str((sq >> 4) + 1)


def parse_square(name: str) -> int:
    return square(_FILES.index(name[0]), int(name[1]) - 1)


# A move is (frm, to, promo) with promo in "qrbn" or "".
def move_uci(move: tuple) -> str:
    frm, to, promo = move
    return square_name(frm) + square_name(to) + promo


# Piece-square tables (values, not code — standard simplified-eval numbers),
# indexed rank*8+file from white's perspective; mirrored for black.
_PST_P = (
      0,   0,   0,   0,   0,   0,   0,   0,
      5,  10,  10, -20, -20,  10,  10,   5,
      5,  -5, -10,   0,   0, -10,  -5,   5,
      0,   0,   0,  20,  20,   0,   0,   0,
      5,   5,  10,  25,  25,  10,   5,   5,
     10,  10,  20,  30,  30,  20,  10,  10,
     50,  50,  50,  50,  50,  50,  50,  50,
      0,   0,   0,   0,   0,   0,   0,   0,
)
_PST_N = (
    -50, -40, -30, -30, -30, -30, -40, -50,
    -40, -20,   0,   5,   5,   0, -20, -40,
    -30,   5,  10,  15,  15,  10,   5, -30,
    -30,   0,  15,  20,  20,  15,   0, -30,
    -30,   5,  15,  20,  20,  15,   5, -30,
    -30,   0,  10,  15,  15,  10,   0, -30,
    -40, -20,   0,   0,   0,   0, -20, -40,
    -50, -40, -30, -30, -30, -30, -40, -50,
)
_PST_B = (
    -20, -10, -10, -10, -10, -10, -10, -20,
    -10,   5,   0,   0,   0,   0,   5, -10,
    -10,  10,  10,  10,  10,  10,  10, -10,
    -10,   0,  10,  10,  10,  10,   0, -10,
    -10,   5,   5,  10,  10,   5,   5, -10,
    -10,   0,   5,  10,  10,   5,   0, -10,
    -10,   0,   0,   0,   0,   0,   0, -10,
    -20, -10, -10, -10, -10, -10, -10, -20,
)
_PST_R = (
      0,   0,   0,   5,   5,   0,   0,   0,
     -5,   0,   0,   0,   0,   0,   0,  -5,
     -5,   0,   0,   0,   0,   0,   0,  -5,
     -5,   0,   0,   0,   0,   0,   0,  -5,
     -5,   0,   0,   0,   0,   0,   0,  -5,
     -5,   0,   0,   0,   0,   0,   0,  -5,
      5,  10,  10,  10,  10,  10,  10,   5,
      0,   0,   0,   0,   0,   0,   0,   0,
)
_PST_Q = (
    -20, -10, -10,  -5,  -5, -10, -10, -20,
    -10,   0,   5,   0,   0,   0,   0, -10,
    -10,   5,   5,   5,   5,   5,   0, -10,
      0,   0,   5,   5,   5,   5,   0,  -5,
     -5,   0,   5,   5,   5,   5,   0,  -5,
    -10,   0,   5,   5,   5,   5,   0, -10,
    -10,   0,   0,   0,   0,   0,   0, -10,
    -20, -10, -10,  -5,  -5, -10, -10, -20,
)
_PST_K = (
     20,  30,  10,   0,   0,  10,  30,  20,
     20,  20,   0,   0,   0,   0,  20,  20,
    -10, -20, -20, -20, -20, -20, -20, -10,
    -20, -30, -30, -40, -40, -30, -30, -20,
    -30, -40, -40, -50, -50, -40, -40, -30,
    -30, -40, -40, -50, -50, -40, -40, -30,
    -30, -40, -40, -50, -50, -40, -40, -30,
    -30, -40, -40, -50, -50, -40, -40, -30,
)
_PST = {"P": _PST_P, "N": _PST_N, "B": _PST_B, "R": _PST_R, "Q": _PST_Q, "K": _PST_K}

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class Board:
    __slots__ = ("sq", "white_to_move", "castling", "ep", "halfmove", "fullmove")

    def __init__(self):
        self.sq: list[str] = [""] * 128
        self.white_to_move = True
        self.castling = ""
        self.ep: int | None = None
        self.halfmove = 0
        self.fullmove = 1

    # -- FEN ---------------------------------------------------------------

    @classmethod
    def from_fen(cls, fen: str) -> "Board":
        board = cls()
        placement, active, castling, ep, *clocks = fen.split()
        for rank_idx, row in enumerate(placement.split("/")):
            rank = 7 - rank_idx
            file = 0
            for ch in row:
                if ch.isdigit():
                    file += int(ch)
                else:
                    board.sq[square(file, rank)] = ch
                    file += 1
        board.white_to_move = active == "w"
        board.castling = castling if castling != "-" else ""
        board.ep = parse_square(ep) if ep != "-" else None
        board.halfmove = int(clocks[0]) if clocks else 0
        board.fullmove = int(clocks[1]) if len(clocks) > 1 else 1
        return board

    @classmethod
    def start(cls) -> "Board":
        return cls.from_fen(START_FEN)

    def to_fen(self) -> str:
        rows = []
        for rank in range(7, -1, -1):
            row, empty = "", 0
            for file in range(8):
                piece = self.sq[square(file, rank)]
                if piece:
                    if empty:
                        row += str(empty)
                        empty = 0
                    row += piece
                else:
                    empty += 1
            if empty:
                row += str(empty)
            rows.append(row)
        return " ".join([
            "/".join(rows),
            "w" if self.white_to_move else "b",
            self.castling or "-",
            square_name(self.ep) if self.ep is not None else "-",
            str(self.halfmove),
            str(self.fullmove),
        ])

    def position_key(self) -> str:
        """Repetition identity: placement + side + castling + ep, no clocks."""
        return " ".join(self.to_fen().split()[:4])

    def copy(self) -> "Board":
        board = Board.__new__(Board)
        board.sq = self.sq[:]
        board.white_to_move = self.white_to_move
        board.castling = self.castling
        board.ep = self.ep
        board.halfmove = self.halfmove
        board.fullmove = self.fullmove
        return board

    # -- attacks -----------------------------------------------------------

    def is_attacked(self, target: int, by_white: bool) -> bool:
        sq_ = self.sq
        if by_white:
            for off in (-15, -17):  # a white pawn one rank down attacks target
                p = target + off
                if not p & 0x88 and sq_[p] == "P":
                    return True
            knight, king, bishop, rook, queen = "N", "K", "B", "R", "Q"
        else:
            for off in (15, 17):
                p = target + off
                if not p & 0x88 and sq_[p] == "p":
                    return True
            knight, king, bishop, rook, queen = "n", "k", "b", "r", "q"
        for off in KNIGHT_OFFSETS:
            p = target + off
            if not p & 0x88 and sq_[p] == knight:
                return True
        for off in KING_DIRS:
            p = target + off
            if not p & 0x88 and sq_[p] == king:
                return True
        for dirs, sliders in ((DIAG_DIRS, (bishop, queen)), (ORTH_DIRS, (rook, queen))):
            for direction in dirs:
                p = target + direction
                while not p & 0x88:
                    piece = sq_[p]
                    if piece:
                        if piece in sliders:
                            return True
                        break
                    p += direction
        return False

    def king_square(self, white: bool) -> int:
        king = "K" if white else "k"
        return self.sq.index(king)

    def in_check(self) -> bool:
        return self.is_attacked(self.king_square(self.white_to_move), not self.white_to_move)

    # -- move generation ---------------------------------------------------

    def pseudo_moves(self, captures_only: bool = False):
        """Pseudo-legal moves (may leave own king in check — the search and
        legal_moves() filter by applying). Castling is fully validated here
        because it needs attack tests anyway."""
        white = self.white_to_move
        own = str.isupper if white else str.islower
        enemy = str.islower if white else str.isupper
        moves = []
        for frm in range(128):
            if frm & 0x88:
                continue
            piece = self.sq[frm]
            if not piece or not own(piece):
                continue
            kind = piece.upper()
            if kind == "P":
                forward = 16 if white else -16
                start_rank = 1 if white else 6
                promo_rank = 7 if white else 0
                one = frm + forward
                if not captures_only and not one & 0x88 and not self.sq[one]:
                    if one >> 4 == promo_rank:
                        moves += [(frm, one, p) for p in "qrbn"]
                    else:
                        moves.append((frm, one, ""))
                        two = one + forward
                        if frm >> 4 == start_rank and not self.sq[two]:
                            moves.append((frm, two, ""))
                for off in ((15, 17) if white else (-15, -17)):
                    to = frm + off
                    if to & 0x88:
                        continue
                    target = self.sq[to]
                    if (target and enemy(target)) or to == self.ep:
                        if to >> 4 == promo_rank:
                            moves += [(frm, to, p) for p in "qrbn"]
                        else:
                            moves.append((frm, to, ""))
            elif kind == "N" or kind == "K":
                for off in KNIGHT_OFFSETS if kind == "N" else KING_DIRS:
                    to = frm + off
                    if to & 0x88:
                        continue
                    target = self.sq[to]
                    if target:
                        if enemy(target):
                            moves.append((frm, to, ""))
                    elif not captures_only:
                        moves.append((frm, to, ""))
            else:
                dirs = {"B": DIAG_DIRS, "R": ORTH_DIRS, "Q": KING_DIRS}[kind]
                for direction in dirs:
                    to = frm + direction
                    while not to & 0x88:
                        target = self.sq[to]
                        if not target:
                            if not captures_only:
                                moves.append((frm, to, ""))
                        else:
                            if enemy(target):
                                moves.append((frm, to, ""))
                            break
                        to += direction
        if not captures_only:
            moves += self._castle_moves()
        return moves

    def _castle_moves(self):
        moves = []
        white = self.white_to_move
        rights = ("K", "Q") if white else ("k", "q")
        home = 0 if white else 7
        king_from = square(4, home)
        if self.sq[king_from] != ("K" if white else "k"):
            return moves
        if self.is_attacked(king_from, not white):
            return moves
        if rights[0] in self.castling:
            f1, g1 = square(5, home), square(6, home)
            if (not self.sq[f1] and not self.sq[g1]
                    and not self.is_attacked(f1, not white)
                    and not self.is_attacked(g1, not white)):
                moves.append((king_from, g1, ""))
        if rights[1] in self.castling:
            b1, c1, d1 = square(1, home), square(2, home), square(3, home)
            if (not self.sq[b1] and not self.sq[c1] and not self.sq[d1]
                    and not self.is_attacked(c1, not white)
                    and not self.is_attacked(d1, not white)):
                moves.append((king_from, c1, ""))
        return moves

    def apply(self, move: tuple) -> "Board":
        frm, to, promo = move
        board = self.copy()
        piece = board.sq[frm]
        white = board.white_to_move
        kind = piece.upper()
        captured = board.sq[to]

        board.sq[frm] = ""
        board.sq[to] = piece
        board.ep = None

        if kind == "P":
            if to == self.ep and not captured:
                # en passant: the captured pawn sits behind the target square
                behind = to - 16 if white else to + 16
                board.sq[behind] = ""
                captured = "p" if white else "P"
            elif abs(to - frm) == 32:
                board.ep = frm + (16 if white else -16)
            if promo:
                board.sq[to] = promo.upper() if white else promo
        elif kind == "K" and abs(to - frm) == 2:
            home = frm & ~0xF
            if to > frm:  # kingside: rook h-file -> f-file
                board.sq[home + 5] = board.sq[home + 7]
                board.sq[home + 7] = ""
            else:  # queenside: rook a-file -> d-file
                board.sq[home + 3] = board.sq[home]
                board.sq[home] = ""

        for touched in (frm, to):
            cleared = _CASTLE_CLEAR.get(touched)
            if cleared:
                board.castling = "".join(c for c in board.castling if c not in cleared)

        board.halfmove = 0 if (kind == "P" or captured) else board.halfmove + 1
        if not white:
            board.fullmove += 1
        board.white_to_move = not white
        return board

    def legal_moves(self) -> list[tuple]:
        mover_white = self.white_to_move
        legal = []
        for move in self.pseudo_moves():
            child = self.apply(move)
            if not child.is_attacked(child.king_square(mover_white), not mover_white):
                legal.append(move)
        return legal

    def is_capture(self, move: tuple) -> bool:
        frm, to, _ = move
        return bool(self.sq[to]) or (self.sq[frm].upper() == "P" and to == self.ep)


# -- SAN (for the move list and the log) ------------------------------------


def san(board: Board, move: tuple) -> str:
    frm, to, promo = move
    piece = board.sq[frm].upper()
    capture = board.is_capture(move)

    if piece == "K" and abs(to - frm) == 2:
        text = "O-O" if to > frm else "O-O-O"
    elif piece == "P":
        text = (square_name(frm)[0] + "x" if capture else "") + square_name(to)
        if promo:
            text += "=" + promo.upper()
    else:
        # Disambiguate against other legal moves of the same piece kind
        # landing on the same square — file first, then rank, then both.
        others = [
            m for m in board.legal_moves()
            if m[1] == to and m[0] != frm and board.sq[m[0]].upper() == piece
        ]
        hint = ""
        if others:
            same_file = any((m[0] & 7) == (frm & 7) for m in others)
            same_rank = any((m[0] >> 4) == (frm >> 4) for m in others)
            if not same_file:
                hint = square_name(frm)[0]
            elif not same_rank:
                hint = square_name(frm)[1]
            else:
                hint = square_name(frm)
        text = piece + hint + ("x" if capture else "") + square_name(to)

    child = board.apply(move)
    if child.in_check():
        text += "#" if not child.legal_moves() else "+"
    return text


# -- perft (test harness) ----------------------------------------------------


def perft(board: Board, depth: int) -> int:
    if depth == 0:
        return 1
    total = 0
    for move in board.legal_moves():
        total += perft(board.apply(move), depth - 1)
    return total


# -- evaluation + search ------------------------------------------------------


def evaluate(board: Board) -> int:
    """Material + piece-square score from the side-to-move's perspective."""
    score = 0
    for sq_ in range(128):
        if sq_ & 0x88:
            continue
        piece = board.sq[sq_]
        if not piece:
            continue
        kind = piece.upper()
        file, rank = sq_ & 7, sq_ >> 4
        if piece.isupper():
            score += PIECE_VALUES[kind] + _PST[kind][rank * 8 + file]
        else:
            score -= PIECE_VALUES[kind] + _PST[kind][(7 - rank) * 8 + file]
    return score if board.white_to_move else -score


class _SearchBudget(Exception):
    """Raised inside the tree when time/nodes run out; the completed
    iteration's move stands."""


class _Search:
    def __init__(self, deadline: float, max_nodes: int):
        self.deadline = deadline
        self.max_nodes = max_nodes
        self.nodes = 0

    def _tick(self):
        self.nodes += 1
        if self.nodes >= self.max_nodes or (
            self.nodes % 512 == 0 and time.monotonic() > self.deadline
        ):
            raise _SearchBudget

    def _ordered(self, board: Board, captures_only: bool = False):
        moves = board.pseudo_moves(captures_only=captures_only)

        def key(move):
            if board.is_capture(move):
                victim = board.sq[move[1]].upper() or "P"  # "" on en passant
                attacker = board.sq[move[0]].upper()
                return -(PIECE_VALUES[victim] * 10 - PIECE_VALUES[attacker])
            return 1
        moves.sort(key=key)
        return moves

    def quiesce(self, board: Board, alpha: int, beta: int) -> int:
        self._tick()
        stand = evaluate(board)
        if stand >= beta:
            return beta
        alpha = max(alpha, stand)
        mover_white = board.white_to_move
        for move in self._ordered(board, captures_only=True):
            child = board.apply(move)
            if child.is_attacked(child.king_square(mover_white), not mover_white):
                continue
            score = -self.quiesce(child, -beta, -alpha)
            if score >= beta:
                return beta
            alpha = max(alpha, score)
        return alpha

    def negamax(self, board: Board, depth: int, alpha: int, beta: int, ply: int) -> int:
        self._tick()
        if depth == 0:
            return self.quiesce(board, alpha, beta)
        mover_white = board.white_to_move
        best = -MATE * 2
        any_legal = False
        for move in self._ordered(board):
            child = board.apply(move)
            if child.is_attacked(child.king_square(mover_white), not mover_white):
                continue
            any_legal = True
            score = -self.negamax(child, depth - 1, -beta, -alpha, ply + 1)
            if score > best:
                best = score
            alpha = max(alpha, score)
            if alpha >= beta:
                break
        if not any_legal:
            # Prefer nearer mates: a mate found sooner scores higher.
            return -MATE + ply if board.in_check() else 0
        return best


def best_move(
    board: Board, depth: int = 3, time_budget: float = 4.0, max_nodes: int = 400_000
) -> tuple | None:
    """Iteratively deepened alpha-beta; returns the best move from the last
    completed iteration (or the best-so-far when the budget interrupts)."""
    legal = board.legal_moves()
    if not legal:
        return None
    search = _Search(time.monotonic() + time_budget, max_nodes)
    best = legal[0]
    for d in range(1, max(1, depth) + 1):
        current, current_score = None, -MATE * 2
        try:
            for move in sorted(legal, key=lambda m: 0 if m == best else 1):
                child = board.apply(move)
                score = -search.negamax(child, d - 1, -MATE * 2, MATE * 2, 1)
                if score > current_score:
                    current, current_score = move, score
        except _SearchBudget:
            if current is not None and current_score > -MATE:
                best = current
            break
        best = current
        if current_score >= MATE - 100:
            break  # forced mate found — deeper search can't improve it
    return best
