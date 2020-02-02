#!/usr/bin/python3
import sys
import os
import signal
from server import SMSServer
from daemons.prefab import run

class SMSServerDaemon(run.RunDaemon):
    def run(self):
        s = SMSServer('', 44444)

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
        else:
            print(f"Operação '{action}' inválida.")
    else:
        print(f"Uso:\n\n{sys.argv[0]} <start|stop|restart>")
