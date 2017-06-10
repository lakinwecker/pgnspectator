import chess
import chess.pgn
import glob
from io import StringIO
import json
from tornado import websocket, web, ioloop, httpclient


games = {}
subscriptions = []

#{"ply":1,"uci":"e2e4","san":"e4","fen":"rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"}
#{"v":23,"t":"move","d":{"uci":"f4f5","san":"f5","fen":"r4rk1/1bq1bppp/p1nppn2/1pp2P2/4P3/P1NP3P/BPP1N1P1/R1BQ1R1K","ply":23,"clock":{"white":569.35,"black":454.36},"dests":{"a8":"a7b8c8d8e8","c6":"b8d8a7a5e5b4d4","e7":"d8","f8":"e8d8c8b8","b5":"b4","c5":"c4","f6":"e8d7d5h5e4g4","e6":"e5f5","g7":"g6g5","b7":"c8","c7":"c8d7b8d8b6a5","d6":"d5","a6":"a5","h7":"h6h5","g8":"h8"}}}

def hacky_python_parsing_of_times(comment):
    assert "[%clk" in comment
    comment = comment.replace("[%clk ", "")
    comment = comment.replace("]", "")
    parts = comment.split(":")
    assert len(parts) == 3
    h,m,s = [int(x) for x in parts]
    return (((h*60) + m)*60)+s


def game_key(game):
    white = game.headers['White']
    black = game.headers['Black']
    key = "{} vs {}".format(white, black)
    return key

def game_message(game):
    last_node = game
    last_ply = 0
    moves = []
    while not last_node.is_end():
        last_node = last_node.variations[0]
        last_ply += 1
        moves.append(last_node)
    if last_node.board().turn == chess.WHITE:
        white_last_move = last_node
        black_last_move = last_node.parent
    else:
        black_last_move = last_node
        white_last_move = last_node.parent

    return {
        "t": "game",
        "d": {
            "game": {
                "id": game_key(game),
                "variant": {"key": "standard", "name": "standard", "short": "Std" },
                "speed": "classical",
                "perf": "classical",
                "rated": True,
                "initialFen": game.board().fen(),
                "fen": last_node.board().fen(),
                "turns": last_ply,
                "source": "norway-2017-arbiter",
                "lastMove": last_node.move.uci(),
                "opening": {
                    "eco": game.headers["ECO"],
                }
            },
            "clock": {
                "running": True,
                "initial": 6000,
                "increment": 0, # lying - but I don't think lichess implements the style of increment
                "white": hacky_python_parsing_of_times(white_last_move.comment),
                "black": hacky_python_parsing_of_times(black_last_move.comment),
            },
            "player": {
                "color": "white",
                "rating": int(game.headers["WhiteElo"]),
                "user": {
                    "id": game.headers['White'],
                    "username": game.headers['White'],
                }
            },
            "opponent": {
                "color": "black",
                "rating": int(game.headers["BlackElo"]),
                "user": {
                    "id": game.headers['Black'],
                    "username": game.headers['Black'],
                }
            },
            "orientation": "white",
            "steps": [move_message(n) for n in moves],
        }
    }

def move_message(node, ply=None):
    return {
        "t": "move",
        "d": {
            "ply": 0 if ply is None else ply,
            "uci": node.move.uci(),
            "san": node.san(),
            "fen": node.board().fen(),
            "clock": {
                "white" if node.board().turn == chess.WHITE else "black": hacky_python_parsing_of_times(node.comment),
            }
        }
    }

def broadcast(message):
    json_message = json.dumps(message)
    print(json_message)
    for subscription in subscriptions:
        self.write_message(json)

class IndexHandler(web.RequestHandler):
    def get(self):
        self.render("index.html")

class SocketHandler(websocket.WebSocketHandler):
    def check_origin(self, origin):
        return True

    def open(self):
        if self not in subscriptions:
            subscriptions.append(self)
            for game in games:
                self.write_message(game_message(game))

    def on_close(self):
        if self in subscriptions:
            subscriptions.remove(self)

app = web.Application([
    (r'/', IndexHandler),
    (r'/ws', SocketHandler),
    (r'/(cg_base.css)', web.StaticFileHandler, {'path': './'}),
    (r'/(cg_theme.css)', web.StaticFileHandler, {'path': './'}),
])

def process_pgn(contents):
    handle = StringIO(contents)
    while True:
        new_game = chess.pgn.read_game(handle)
        if new_game is None:
            break
        key = game_key(new_game)
        if key not in games:
            games[key] = new_game
            print("inserting {}".format(key))
            broadcast(game_message(new_game))
            # BROADCAST TO CLIENTS OF NEW GAME
            continue

        old_game = games[key]
        old_node = old_game.variations[0]
        new_node = new_game.variations[0]
        while not old_node.is_end() and not new_node.is_end():
            if old_node.move.uci() != new_node.move.uci():
                print("Corruption! Restart game: {}".format(key))
                continue
            old_node = old_node.variations[0]
            new_node = new_node.variations[0]
        if old_node.is_end() and new_node.is_end():
            # print("No new moves for {}".format(key))
            continue
        if not old_node.is_end() and new_node.is_end():
            print(old_game, new_game)
            print(old_node, new_node)
            print("Corruption! old game is longer than new game!? {}".format(key))
            continue
        while not new_node.is_end():
            # print("New move in {}: {}".format(key, .move.uci()))
            broadcast(move_message(new_node))
            new_node = new_node.variations[0]
        games[key] = new_game

def update_pgns():
    pass

already_processed = []
def poll_files():
    files = sorted(glob.glob("./local-files/*.pgn"))
    for file in files:
        if file in already_processed:
            continue
        print("Processing {}!".format(file))
        already_processed.append(file)
        contents = open(file, "r").read()
        process_pgn(contents)
        return

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        url = sys.argv[1]
        pollpgn = ioloop.PeriodicCallback(update_pgns, 1000)
    else:
        print("Polling files!")
        pollpgn = ioloop.PeriodicCallback(poll_files, 500)
    pollpgn.start()
    app.listen(8888)
ioloop.IOLoop.instance().start()
