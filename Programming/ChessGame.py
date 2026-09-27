"""
项目名称: 中国象棋游戏
创建日期: 2025-12-08
需求文件: data/ChessGameAsset

依赖库:
pygame==2.6.1
"""

import sys
from abc import ABC, abstractmethod

import pygame


class MoveStrategy(ABC):
    @abstractmethod
    def movement(self, current_pos, target_pos, board, current_player):
        pass


class PieceFactory:
    @staticmethod
    def create_piece(color, piece_type, pos):
        strategies = {
            "j": General,
            "s": Advisor,
            "x": Elephant,
            "c": Chariot,
            "m": Horse,
            "p": Cannon,
            "z": Pawn,
        }
        return strategies[piece_type](color, piece_type, pos)


class ChessPiece:
    def __init__(self, color, piece_type, pos):
        self.color = color
        self.piece_type = piece_type
        self.pos = pos
        self.select = False
        self.move_strategy = None

    def draw_piece(self, cell_size, screen):
        pos = cell_size * (self.pos[0] + 0.5), cell_size * (self.pos[1] + 0.5)
        image = pygame.image.load(
            f"data/ChessGameAsset/{self.color}_{self.piece_type}.png"
        )
        image = pygame.transform.scale(image, (cell_size, cell_size))
        screen.blit(image, pos)
        if self.select:
            frame = pygame.image.load(f"data/ChessGameAsset/{self.color}_selected.png")
            frame = pygame.transform.scale(frame, (cell_size, cell_size))
            screen.blit(frame, pos)

    def movement(self, target_pos, board, current_player):
        return self.move_strategy.movement(self.pos, target_pos, board, current_player)


class GeneralStrategy(MoveStrategy):
    def movement(self, current_pos, target_pos, board, current_player):
        current_x, current_y = current_pos
        target_x, target_y = target_pos
        if current_player == "r":
            return (3 <= target_x <= 5 and 7 <= target_y <= 9) and abs(
                current_x - target_x
            ) + abs(current_y - target_y) == 1
        else:
            return (3 <= target_x <= 5 and 0 <= target_y <= 2) and abs(
                current_x - target_x
            ) + abs(current_y - target_y) == 1


class AdvisorStrategy(MoveStrategy):
    def movement(self, current_pos, target_pos, board, current_player):
        current_x, current_y = current_pos
        target_x, target_y = target_pos
        if current_player == "r":
            return (
                (3 <= target_x <= 5 and 7 <= target_y <= 9)
                and abs(current_y - target_y) == 1
                and abs(current_x - target_x) == 1
            )
        else:
            return (
                (3 <= target_x <= 5 and 0 <= target_y <= 2)
                and abs(current_y - target_y) == 1
                and abs(current_x - target_x) == 1
            )


class ElephantStrategy(MoveStrategy):
    def movement(self, current_pos, target_pos, board, current_player):
        current_x, current_y = current_pos
        target_x, target_y = target_pos
        if current_player == "r":
            return (
                target_y >= 5
                and abs(current_x - target_x) == 2
                and abs(current_y - target_y) == 2
                and board[(current_y + target_y) // 2][(current_x + target_x) // 2]
                is None
            )
        else:
            return (
                target_y <= 4
                and abs(current_x - target_x) == 2
                and abs(current_y - target_y) == 2
                and board[(current_y + target_y) // 2][(current_x + target_x) // 2]
                is None
            )


class ChariotStrategy(MoveStrategy):
    def movement(self, current_pos, target_pos, board, current_player):
        current_x, current_y = current_pos
        target_x, target_y = target_pos
        if current_x == target_x:
            start, end = min(current_y, target_y) + 1, max(current_y, target_y)
            for y in range(start, end):
                if board[y][current_x] is not None:
                    return False
            return True
        elif current_y == target_y:
            start, end = min(current_x, target_x) + 1, max(current_x, target_x)
            for x in range(start, end):
                if board[current_y][x] is not None:
                    return False
            return True
        return False


class HorseStrategy(MoveStrategy):
    def movement(self, current_pos, target_pos, board, current_player):
        current_x, current_y = current_pos
        target_x, target_y = target_pos
        if abs(current_x - target_x) == 2 and abs(current_y - target_y) == 1:
            return board[current_y][(current_x + target_x) // 2] is None
        elif abs(current_y - target_y) == 2 and abs(current_x - target_x) == 1:
            return board[(current_y + target_y) // 2][current_x] is None
        return False


class CannonStrategy(MoveStrategy):
    def movement(self, current_pos, target_pos, board, current_player):
        current_x, current_y = current_pos
        target_x, target_y = target_pos
        if current_x == target_x:
            start, end = min(current_y, target_y) + 1, max(current_y, target_y)
            count = 0
            for y in range(start, end):
                if board[y][current_x] is not None:
                    count += 1
            if board[target_y][target_x] is None:
                return count == 0
            else:
                return count == 1
        elif current_y == target_y:
            start, end = min(current_x, target_x) + 1, max(current_x, target_x)
            count = 0
            for x in range(start, end):
                if board[current_y][x] is not None:
                    count += 1
            if board[target_y][target_x] is None:
                return count == 0
            else:
                return count == 1
        return False


class PawnStrategy(MoveStrategy):
    def movement(self, current_pos, target_pos, board, current_player):
        current_x, current_y = current_pos
        target_x, target_y = target_pos
        if current_player == "r":
            return (target_x == current_x and target_y == current_y - 1) or (
                abs(target_x - current_x) == 1
                and current_y == target_y
                and current_y <= 4
            )
        else:
            return (target_x == current_x and target_y == current_y + 1) or (
                abs(target_x - current_x) == 1
                and current_y == target_y
                and current_y >= 5
            )


class General(ChessPiece):
    def __init__(self, color, piece_type, pos):
        super().__init__(color, piece_type, pos)
        self.move_strategy = GeneralStrategy()


class Advisor(ChessPiece):
    def __init__(self, color, piece_type, pos):
        super().__init__(color, piece_type, pos)
        self.move_strategy = AdvisorStrategy()


class Elephant(ChessPiece):
    def __init__(self, color, piece_type, pos):
        super().__init__(color, piece_type, pos)
        self.move_strategy = ElephantStrategy()


class Chariot(ChessPiece):
    def __init__(self, color, piece_type, pos):
        super().__init__(color, piece_type, pos)
        self.move_strategy = ChariotStrategy()


class Horse(ChessPiece):
    def __init__(self, color, piece_type, pos):
        super().__init__(color, piece_type, pos)
        self.move_strategy = HorseStrategy()


class Cannon(ChessPiece):
    def __init__(self, color, piece_type, pos):
        super().__init__(color, piece_type, pos)
        self.move_strategy = CannonStrategy()


class Pawn(ChessPiece):
    def __init__(self, color, piece_type, pos):
        super().__init__(color, piece_type, pos)
        self.move_strategy = PawnStrategy()


class ChessBoard:
    def __init__(self):
        self.board = [[None] * 9 for _ in range(10)]
        self.init_board()
        self.current_player = "r"
        self.win = False
        self.selected_piece = None

    def init_board(self):
        board = [
            ["b_c", "b_m", "b_x", "b_s", "b_j", "b_s", "b_x", "b_m", "b_c"],
            [None, None, None, None, None, None, None, None, None],
            [None, "b_p", None, None, None, None, None, "b_p", None],
            ["b_z", None, "b_z", None, "b_z", None, "b_z", None, "b_z"],
            [None, None, None, None, None, None, None, None, None],
            [None, None, None, None, None, None, None, None, None],
            ["r_z", None, "r_z", None, "r_z", None, "r_z", None, "r_z"],
            [None, "r_p", None, None, None, None, None, "r_p", None],
            [None, None, None, None, None, None, None, None, None],
            ["r_c", "r_m", "r_x", "r_s", "r_j", "r_s", "r_x", "r_m", "r_c"],
        ]
        for y in range(10):
            for x in range(9):
                if board[y][x]:
                    self.board[y][x] = PieceFactory.create_piece(
                        board[y][x][0], board[y][x][-1], (x, y)
                    )

    def draw_board(self, cell_size, screen):
        screen.fill((255, 127, 38))
        board = pygame.image.load("data/ChessGameAsset/board.png")
        board = pygame.transform.scale(board, (cell_size * 9, cell_size * 10))
        screen.blit(board, (cell_size * 0.5, cell_size * 0.5))
        for y in range(10):
            for x in range(9):
                if self.board[y][x]:
                    self.board[y][x].draw_piece(cell_size, screen)
        if self.win:
            win = pygame.image.load(f"data/ChessGameAsset/win.png")
            win = pygame.transform.scale(win, (cell_size * 2.5, cell_size))
            screen.blit(win, (cell_size * 3.75, cell_size * 5))
        else:
            if self.current_player == "r":
                current_player = pygame.image.load(f"data/ChessGameAsset/r_turn.png")
            else:
                current_player = pygame.image.load(f"data/ChessGameAsset/b_turn.png")
            current_player = pygame.transform.scale(
                current_player, (cell_size * 2.5, cell_size)
            )
            screen.blit(current_player, (cell_size * 3.75, cell_size * 5))

    def select_piece(self, pos):
        target_x, target_y = pos
        if not self.win:
            if self.selected_piece:
                if (
                    self.board[target_y][target_x]
                    and self.current_player == self.board[target_y][target_x].color
                ):
                    self.selected_piece.select = False
                    self.selected_piece = self.board[target_y][target_x]
                    self.selected_piece.select = True
                else:
                    movement = self.selected_piece.movement(
                        pos, self.board, self.current_player
                    )
                    if movement:
                        self.move_piece(pos)
                    else:
                        self.selected_piece.select = False
                        self.selected_piece = None
            else:
                if (
                    self.board[target_y][target_x]
                    and self.current_player == self.board[target_y][target_x].color
                ):
                    self.selected_piece = self.board[target_y][target_x]
                    self.selected_piece.select = True

    def move_piece(self, pos):
        target_x, target_y = pos
        current_x, current_y = self.selected_piece.pos
        if (
            self.board[target_y][target_x]
            and self.board[target_y][target_x].piece_type == "j"
        ):
            self.win = True
        self.board[target_y][target_x], self.board[current_y][current_x] = (
            self.selected_piece,
            None,
        )
        self.selected_piece.pos = pos
        self.selected_piece.select = False
        self.selected_piece = None
        pygame.mixer.music.load("data/ChessGameAsset/move.mp3")
        pygame.mixer.music.play()
        self.current_player = "b" if self.current_player == "r" else "r"


class ChessGame:
    def __init__(self):
        pygame.init()
        self.cell_size = pygame.display.Info().current_h // 16
        self.screen = pygame.display.set_mode(
            (self.cell_size * 10, self.cell_size * 11)
        )
        pygame.display.set_caption("Chinese_Chess_Game")

    def run(self):
        chessboard = ChessBoard()
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    sys.exit()
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    pos = int(event.pos[0] / self.cell_size - 0.5), int(
                        event.pos[1] / self.cell_size - 0.5
                    )
                    if 0 <= pos[0] <= 8 and 0 <= pos[1] <= 9:
                        chessboard.select_piece(pos)
            chessboard.draw_board(self.cell_size, self.screen)
            pygame.display.update()


if __name__ == "__main__":
    game = ChessGame()
    game.run()
