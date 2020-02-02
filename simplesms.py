#!/usr/bin/python3
import sys
import os
import signal
import sqlite3
from server import SMSServer
from daemons.prefab import run

SMSSERVER_DB = 'smsserver.db'

def create_connection():
    try:
        con = sqlite3.connect(database=SMSSERVER_DB, timeout=5)
    except sqlite3.Error as e:
        print(f"Não foi possível conectar-se ao banco de dados local '{SMSSERVER_DB}': {e}.")

    return con

class SMSServerDaemon(run.RunDaemon):
    def run(self):
        s = SMSServer('', 44444, database=SMSSERVER_DB)

        # Ao recebermos o sinal SIGUSR1, faremos "reload" da aplicação
        # Ex: ao chegar um pedido de envio de SMS novo ou para atualizar as configurações internas
        signal.signal(signal.SIGUSR1, lambda a, b: s.do_refresh())

        s.listen()

if __name__ == '__main__':
    if len(sys.argv) >= 2:
        action = sys.argv[1]
        pidfile = "/tmp/sleepy.pid"

        d = SMSServerDaemon(pidfile=pidfile)

        if action == "start":
            d.start()
        elif action == "stop":
            d.stop()
        elif action == "restart":
            d.restart()
        elif action == "refresh":
            if d.pid:
                os.kill(d.pid, signal.SIGUSR1)
            else:
                print(f"O processo precisa estar executando para efetuar um '{action}'.")
        elif action == "createdb":
            con = create_connection()
            assert con

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
    number VARCHAR(16) NOT NULL,
    message VARCHAR(3000) NOT NULL,
    FOREIGN KEY (username) REFERENCES sms_client(username)
);
""")
            con.close()
        elif action == "useradd":
            if len(sys.argv) < 4:
                print("É preciso informar USERNAME e PASSWORD.")
            else:
                con = create_connection()
                assert con
                cur = con.cursor()
                cur.execute("INSERT INTO sms_client (username, password) VALUES (?, ?)", (sys.argv[2], sys.argv[3]))
                con.commit()
                con.close()
        elif action == "userlist":
            con = create_connection()
            assert con
            cur = con.cursor()
            cur.execute("SELECT username FROM sms_client")
            row = cur.fetchone()
            
            while row:
                print(f"{row[0]}")
                row = cur.fetchone()
            
            con.close()
        else:
            print(f"Operação '{action}' inválida.")
    else:
        print(f"Uso:\n\n{sys.argv[0]} <start|stop|restart|refresh|createdb|useradd>")
