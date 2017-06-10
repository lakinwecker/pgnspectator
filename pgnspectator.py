# pgnspectator - Broadcast moves based on a PGN feed.
#
# Copyright (C) 2017 Lakin Wecker <lakin@wecker.ca>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.  

import chess
import chess.pgn
import glob
from io import StringIO
import json
import requests
from tornado import websocket, web, ioloop, httpclient
import time


games = {}
subscriptions = []

#{"ply":1,"uci":"e2e4","san":"e4","fen":"rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"}
#{"v":23,"t":"move","d":{"uci":"f4f5","san":"f5","fen":"r4rk1/1bq1bppp/p1nppn2/1pp2P2/4P3/P1NP3P/BPP1N1P1/R1BQ1R1K","ply":23,"clock":{"white":569.35,"black":454.36},"dests":{"a8":"a7b8c8d8e8","c6":"b8d8a7a5e5b4d4","e7":"d8","f8":"e8d8c8b8","b5":"b4","c5":"c4","f6":"e8d7d5h5e4g4","e6":"e5f5","g7":"g6g5","b7":"c8","c7":"c8d7b8d8b6a5","d6":"d5","a6":"a5","h7":"h6h5","g8":"h8"}}}

def hacky_python_parsing_of_times(comment):
    if not "[%clk" in comment:
        return None
    comment = comment.replace("[%clk ", "")
    comment = comment.replace("]", "")
    parts = comment.split(":")
    assert len(parts) == 3
    h,m,s = [int(x) for x in parts]
    return (((h*60) + m)*60)+s


def game_key(game):
    white = "-".join(game.headers['White'].replace(",", "").split())
    black = "-".join(game.headers['Black'].replace(",", "").split())
    key = "{}-vs-{}".format(white.lower(), black.lower())
    return key

# {"t":"fen","d":{"id":"e1sdy465","fen":"8/8/R3B1P1/8/1k5p/p7/6K1/r7","lm":"g5g6"}}
def start_game_message(game):
    game_json = game_message(game)['d']
    return {
        "t": "fen",
        "d": {
            "id": game_json['game']['id'],
            "fen": game_json['game']['fen'],
            "lm": game_json['game']['lastMove'],
        }
    }
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

# {"t":"fen","d":{"id":"CdDDXCJd","fen":"2r1rbk1/3b1ppp/q2p1n2/1pnPp3/p1N1P3/2N1B1PP/PP2QPB1/2R1R1K1","lm":"b7b5"}}
# {"t": "fen", "d": {"ply": 0, "id": "caruana-fabiano-vs-kramnik-vladimir", "fen": "7r/8/1br3p1/p4p2/R2PpNkP/2P1KpP1/1P3P2/R7 w - - 0 45", "san": "Rxc6", "uci": "d6c6", "clock": {"white": 1494}, "lm": "d6c6"}}
def move_message(node, ply=None, type="move"):
    return {
        "t": type,
        "d": {
            "id": game_key(node.root()),
            "ply": 0 if ply is None else ply,
            "uci": node.move.uci(),
            "lm": node.move.uci(),
            "san": node.san(),
            "fen": node.board().fen(),
            "clock": {
                "white" if node.board().turn == chess.WHITE else "black": hacky_python_parsing_of_times(node.comment),
            }
        }
    }

def process_pgn(contents):
    handle = StringIO(contents)
    while True:
        new_game = chess.pgn.read_game(handle)
        if new_game is None:
            break
        key = game_key(new_game)
        new_game.key = key
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
            print("New move in {}: {}".format(key, new_node.move.uci()))
            broadcast(move_message(new_node, type="fen"))
            new_node = new_node.variations[0]
        games[key] = new_game

def update_pgns():
    url = "{}?v={}".format(sys.argv[1], time.time())
    print("GET: {}".format(url))
    response = requests.get(url)
    process_pgn(response.text)

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
        return # don't process anymore.

def broadcast(message):
    for subscription in subscriptions:
        subscription.write_json(message)

class IndexHandler(web.RequestHandler):
    def get(self):
        self.render("index.html", **{
            "games_json": [game_message(game)['d'] for id, game in games.items()],
        })

class SocketHandler(websocket.WebSocketHandler):
    def check_origin(self, origin):
        return True

    def write_json(self, message):
        self.write_message(json.dumps(message))

    def open(self):
        if self not in subscriptions:
            subscriptions.append(self)

    def on_close(self):
        if self in subscriptions:
            subscriptions.remove(self)

    def on_message(self, message):
        message = json.loads(message)
        if message['t'] == 'p':
            self.write_json({"t": "n"})
        if message['t'] == 'startWatching':
            print("startWatching!!")
            ids = message['d'].split()
            for id in ids:
                print(id)
                game = games.get(id, None)
                if game:
                    self.write_json(start_game_message(game))

app = web.Application([
    (r'/', IndexHandler),
    (r'/socket', SocketHandler),
    (r'/(cg_base.css)', web.StaticFileHandler, {'path': './'}),
    (r'/(cg_theme.css)', web.StaticFileHandler, {'path': './'}),
])

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        pollpgn = ioloop.PeriodicCallback(update_pgns, 1000)
    else:
        print("Polling files!")
        pollpgn = ioloop.PeriodicCallback(poll_files, 500)
    pollpgn.start()
    app.listen(8888)
ioloop.IOLoop.instance().start()
