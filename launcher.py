#!/usr/bin/python3
import sys
import os
import signal
import sqlite3
from daemons.prefab import run
from smsserver.core import SMSServer
from smsserver.core import SMSRequestState

SMSSERVER_DIR     = os.path.dirname(os.path.abspath(__file__))
SMSSERVER_DB      = os.path.join(SMSSERVER_DIR, 'smsserver.db')
SMSSERVER_PIDFILE = os.path.join(SMSSERVER_DIR, 'smsserver.pid')
SMSSERVER_LOGFILE = os.path.join(SMSSERVER_DIR, 'smsserver.log')


class SMSServerDaemon(run.RunDaemon):
    def run(self):
        s = SMSServer('', 44444, database=SMSSERVER_DB, logfile=SMSSERVER_LOGFILE)

        # Ao recebermos o sinal SIGUSR1, faremos "reload" da aplicação
        # Ex: ao chegar um pedido de envio de SMS novo ou para atualizar as configurações internas
        signal.signal(signal.SIGUSR1, lambda a, b: s.do_refresh())

        s.listen()

def create_connection():
    try:
        con = sqlite3.connect(SMSSERVER_DB, timeout=5)
    except sqlite3.Error as e:
        fatal(f"Não foi possível conectar-se ao banco de dados local '{SMSSERVER_DB}': {e}.")

    return con

def fatal(message):
    print(f"FATAL: {message}")
    sys.exit(1)

def db_createdb():
    con = create_connection()
    cur = con.cursor()
    cur.executescript(
"""
CREATE TABLE IF NOT EXISTS sms_client (
    username VARCHAR(32) NOT NULL PRIMARY KEY,
    password VARCHAR(64) NOT NULL
);

CREATE TABLE IF NOT EXISTS sms_request (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(32) NOT NULL,
    message VARCHAR(3000) NOT NULL,
    state TINYINT NOT NULL DEFAULT 0,
    date_requested TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (username) REFERENCES sms_client(username)
);

CREATE TABLE IF NOT EXISTS sms_requestnumber (
    request_id INTEGER NOT NULL,
    number VARCHAR(16) NOT NULL,
    state TINYINT NOT NULL DEFAULT 0,
    FOREIGN KEY (request_id) REFERENCES sms_request(id)
);
""")

    con.commit()
    con.close()

def db_createuser(username, password):
    con = create_connection()
    cur = con.cursor()
    cur.execute("INSERT INTO sms_client (username, password) VALUES (?, ?)", (username, password))
    con.commit()
    con.close()

def db_listusers():
    usernames = []
    
    con = create_connection()
    cur = con.cursor()
    cur.execute("SELECT username FROM sms_client")
    row = cur.fetchone()
            
    while row:
        usernames.append(row[0])
        row = cur.fetchone()
            
    con.close()

    return usernames

def db_listsms():
    requests = []
    
    con = create_connection()
    cur = con.cursor()
    cur.execute("SELECT req.state, req.username, reqn.number, req.message, reqn.state FROM sms_request AS req LEFT JOIN sms_requestnumber AS reqn ON reqn.request_id = req.id ORDER BY date_requested ASC")
    row = cur.fetchone()
            
    while row:
        requests.append((row[0], row[1], row[2], row[3], row[4]))
        row = cur.fetchone()
            
    con.close()

    return requests

def db_createrequest(auth, text, numbers):
    con = create_connection()
    cur = con.cursor()
    cur.execute("INSERT INTO sms_request (username, message) VALUES (?, ?)", 
        (
            auth,  
            text
        )
    )

    lid = cur.lastrowid

    for number in numbers:
        cur.execute("INSERT INTO sms_requestnumber (request_id, number) VALUES (?, ?)", 
            (
                lid,  
                number
            )
        )

    con.commit()
    con.close()

def cli_dbmanager(args, d):
    act = '' if len(args) < 2 else args[1]

    if act == "create":
        db_createdb()
    elif act == "useradd":
        if len(args) < 4:
            fatal(f"É preciso informar <username> e <password>")

        db_createuser(args[2], args[3])
        
        if d.pid:
            os.kill(d.pid, signal.SIGUSR1)
    elif act == "userlist":
        users = db_listusers()

        for username in users:
            print(username)
    else:
        print(f"Uso:\n\n")

        for use in ("create","useradd <username> <password>", "userlist"):
            print(f"{sys.argv[0]} {args[0]} {use}")

def cli_smsmanager(args, d):
    act = '' if len(args) < 2 else args[1]

    if act == "send":
        if len(args) < 5:
            fatal(f"É preciso informar <auth_id> <text> <number1> [numberN]...")

        if not d.pid:
            fatal(f"O processo precisa estar executando para efetuar um '{action}'.")

        db_createrequest(args[2], args[3], args[4:])

        os.kill(d.pid, signal.SIGUSR1)
    elif act == "pending":
        fatal("A ser implementado.")
    elif act == "list":
        for state, username, number, text, linestate in db_listsms():
            print(f"[{SMSRequestState(state).name}] {username} -> {number} [{SMSRequestState(linestate).name}] : '{text}'")
    else:
        for use in ("send <auth_id> <text> <number1> [numberN]...", "pending"):
            print(f"{sys.argv[0]} {args[0]} {use}")

if __name__ == '__main__':
    if len(sys.argv) >= 2:
        action = sys.argv[1]

        d = SMSServerDaemon(pidfile=SMSSERVER_PIDFILE)

        if action == "start":
            d.start()
        elif action == "stop":
            d.stop()
        elif action == "restart":
            d.restart()
        elif action == "sync":
            if not d.pid:
                fatal(f"O processo precisa estar executando para efetuar um '{action}'.")
            
            os.kill(d.pid, signal.SIGUSR1)
        elif action == "db":
            cli_dbmanager(sys.argv[1:], d)
        elif action == "sms":
            cli_smsmanager(sys.argv[1:], d)
    else:
        print(f"Uso:\n\n{sys.argv[0]} <start|stop|restart|sync|db|sms> [arg1] [arg2]...")
