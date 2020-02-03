#!/usr/bin/python3
import socket
import sys
import select
import time
import logging
import sqlite3
from datetime import datetime
from enum import Enum, auto

def string_values(list):
    return [str(i) for i in list]

class SMSRequestState(Enum):
    REQUESTED = 0
    PROCESSING = 1
    DONE = 2
    FAILED = 3

class SMSPacketState(Enum):
    SEND = auto()
    SENT = auto()
    RECEIVED = auto()

class SMSServerParseError(Exception):
    pass

class SMSServerMissingArgumentError(Exception):
    pass

class SMSServerInvalidArgumentError(Exception):
    pass

class SMSPacket:
    def __init__(self, action, args, state=SMSPacketState.SEND):
        self.action = action
        self.args = args
        self.state = state

    def generate(self):
        return self.action.encode('utf-8') + b" " + " ".join(string_values(self.args)).encode('utf-8') + b"\n"

class SMSTransactionStep:
    def __init__(self, txpacket, expected=lambda x: x.action != "ERROR", rxpacket=None):
        self.txpacket = txpacket
        self.rxpacket = rxpacket
        self.excepted = expected

    def valid_response(self):
        return self.rxpacket and self.excepted(self.rxpacket)

    def __str__(self):
        ret = f"SMSTransactionStep: txpacket: {self.txpacket.action} {self.txpacket.args}"

        if self.rxpacket:
            ret += f", rxpackets={self.rxpacket.action}:{self.rxpacket.args}"

        ret += f", valid_response={self.valid_response()}"

        return ret

class SMSTransactionBundleStep(SMSTransactionStep):
    def __init__(self, txpacket, expected=lambda x: x.action != "ERROR"):
        super().__init__(txpacket, expected, list())

        self.wait_until = 0

    def wait_for(self, seconds):
        self.wait_until = datetime.timestamp(datetime.now()) + seconds

    def is_waiting(self):
        return self.wait_until > datetime.timestamp(datetime.now())

    def valid_response(self):
        return self.rxpacket and self.excepted(self.rxpacket[-1])

    def __str__(self):
        ret = f"SMSTransactionBundleStep: txpacket: {self.txpacket.action} {self.txpacket.args}, rxpackets=["
        
        for packet in self.rxpacket:
            ret += f"{packet.action}:{packet.args}, "
        
        ret += f"], valid_response={self.valid_response()}"

        return ret

class SMSTransactionBundle:
    def __init__(self, steps, expected):
        self.steps = steps
        self.expected = expected

    def valid_response(self):
        return self.steps and self.expected(self)

    def __str__(self):
        ret = "SMSTransactionBundle: [\n"

        for step in self.steps:
            ret += f"\t{step}\n"

        ret += "]\n"

        return ret

class SMSTransaction:
    def __init__(self, tid, sms_client, steps, timeout=60):
        self.id = tid
        self.sms_client = sms_client
        self.steps = steps
        self.timeout = timeout
        self.start_time = datetime.timestamp(datetime.now())
        self.last_time = self.start_time

    def get_current_step(self):
        if self.steps:
            return self.steps[0]

    def pop_current_step(self):
        if self.steps:
            return self.steps.pop(0)

    def set_current_response(self, rxpacket):
        currstep = self.get_current_step()

        if currstep:
            if isinstance(currstep, SMSTransactionStep):
                currstep.rxpacket = rxpacket
            elif isinstance(currstep, SMSTransactionBundle):
                try:
                    i = int(rxpacket.args[1]) - 1
                except ValueError:
                    raise SMSServerInvalidArgumentError("Esperado pelo índice de sequência da SMSTransactionBundle, porém não estava em um formato correto")
                except IndexError:
                    raise SMSServerMissingArgumentError("Esperado pelo índice de sequência da SMSTransactionBundle, porém não recebido")

                if i >= 0 and i < len(currstep.steps):
                    currstep.steps[i].rxpacket.append(rxpacket)
                else:
                    raise SMSServerInvalidArgumentError("Esperado pelo índice de sequência da SMSTransactionBundle, porém o índice não existe")

    def is_alive(self):
        return self.last_time + self.timeout > datetime.timestamp(datetime.now())

    def __str__(self):
        ret = f"SMSTransaction: [\n"
        
        for step in self.steps:
            ret += f"\t{step}\n"
        
        ret += f"], is_alive={self.is_alive()}"

        return ret

class SMSAction:
    # @DEBUG: Apenas para testes, retirar isso e começar do 0
    AUTO_INCREMENT = 0

    def __init__(self, id, sms_client):
        self.id = id
        self.sms_client = sms_client

    @staticmethod
    def generate_id():
        if SMSAction.AUTO_INCREMENT == 65536:
            SMSAction.AUTO_INCREMENT = 0

        SMSAction.AUTO_INCREMENT += 1

        return SMSAction.AUTO_INCREMENT

    @staticmethod
    def get_sendsms_action(sms_client, message, numbers):
        return SendSMSAction(SMSAction.generate_id(), sms_client, message, numbers)

class SendSMSAction(SMSAction):
    def __init__(self, id, sms_client, message, numbers):
        super().__init__(id, sms_client)

        if message and numbers and len(message.encode('utf-8')) <= 3000:
            self.message = message
            self.numbers = numbers
        else:
            raise ValueError("Mensagem de tamanho inválido ou lista de números vazia")

class SMSAuthClient:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.connected = []
        self.last_synced = 0

    def authenticate(self, username, password, srcaddr):
        if username != self.username or password != self.password:
            return None

        c = self.get_client_from(srcaddr)

        if c:
            c.heartbeat(srcaddr)
        else:
            c = SMSClient(srcaddr, self)
            self.connected.append(c)

        return c

    def get_client_from(self, srcaddr):
        for c in self.connected:
            if c.srcaddr == srcaddr:
                return c

        return None

class SMSClient:
    def __init__(self, srcaddr, auth_client, expires=60):
        self.srcaddr = srcaddr
        self.last_heartbeat = datetime.timestamp(datetime.now())
        self.expires = expires
        self.auth_client = auth_client

        # Define valores vazios
        self.update()

    def is_alive(self):
        return  self.last_heartbeat + self.expires > datetime.timestamp(datetime.now())

    def heartbeat(self, srcaddr=None):
        self.last_heartbeat = datetime.timestamp(datetime.now())
        
        if srcaddr:
            self.srcaddr = srcaddr

    def update(self, package={}):
        self.num = package.get('num', '')
        
        try:
            self.signal = int(package.get('signal', 0))
        except ValueError:
            pass
        
        self.gsm_status = package.get('gsm_status', '')
        self.voip_status = package.get('voip_status', '')
        self.voip_state = package.get('voip_state', '')
        
        try:
            self.remain_time = int(package.get('remain_time', -1))
        except ValueError:
            pass
        
        self.imei = package.get('imei', '')
        self.imsi = package.get('imsi', '')
        self.iccid = package.get('iccid', '')
        self.pro = package.get('pro', '')

    def send_package(self, currsocket, package):
        packagestr = SMSServer.create_package(package)
        currsocket.sendto(packagestr.encode('utf-8'), self.srcaddr)

    def send_packet(self, currsocket, packet):
        currsocket.sendto(packet.generate(), self.srcaddr)
        packet.state = SMSPacketState.SENT

class SMSServer:
    def __init__(self, bindaddr, port, buffersize=2048, database='smsserver.db', logfile=''):
        self.bindaddr = bindaddr
        self.port = port
        self.soc = None
        self.buffersize = buffersize
        self.database = database
        
        self.auth_clients = {}
        self.pending_transactions = []
    
        self.max_consecutive_packages = 5

        if logfile:
            logging.basicConfig(
                filename=logfile,
                format="[%(asctime)s] %(levelname)s -> %(message)s",
                datefmt="%d/%m/%Y %H:%M:%S",
                level=logging.DEBUG
            )
        
        self.do_refresh()

    def cleanup_unsynchronized(self, timestamp):
        for key, auth in self.auth_clients.items():
            if auth.last_synced != timestamp:
                del self.auth_clients[key]

    def fetch_authorized_clients(self, conn):
        last_syncd = datetime.timestamp(datetime.now())

        cur = conn.cursor()
        cur.execute("SELECT username, password FROM sms_client")

        row = cur.fetchone()
        while row:
            if not row[0] in self.auth_clients:
                self.auth_clients[row[0]] = SMSAuthClient(row[0], row[1])
            else:
                self.auth_clients[row[0]].password = row[1]

            self.auth_clients[row[0]].last_synced = last_syncd
            row = cur.fetchone()

        cur.close()

        self.cleanup_unsynchronized(last_syncd)

    def fetch_requests(self, conn):
        cur = conn.cursor()
        cur.execute(f"SELECT id, username, number, message FROM sms_request WHERE state = {SMSRequestState.REQUESTED.value} ORDER BY date_requested DESC")

        updt = conn.cursor()
        row = cur.fetchone()
        while row:
            if row[1] in self.auth_clients and self.auth_clients[row[1]].connected:
                auth_client = self.auth_clients[row[1]]

                for sms_client in auth_client.connected:
                    self.begin_transaction_for(
                        sms_client, 
                        SMSAction.get_sendsms_action(
                            sms_client, 
                            row[3], 
                            [row[2]]
                        )
                    )
                    
                    updt.execute(f"UPDATE sms_request SET state = {SMSRequestState.PROCESSING.value} WHERE id = {row[0]}")
            else:
                self.log(f"Ignorando novo pedido, ID inexistente ou nenhum cliente atualmente associado ao ID.")
                updt.execute(f"UPDATE sms_request SET state = {SMSRequestState.FAILED.value} WHERE id = {row[0]}")

            row = cur.fetchone()

        conn.commit()
        updt.close()
        cur.close()

    def do_refresh(self):
        conn = None
    
        try:
            conn = sqlite3.connect(
                self.database,
                timeout=5
            )

            self.fetch_authorized_clients(conn)
            self.fetch_requests(conn)
        except sqlite3.Error as e:
            self.log(f"Não foi possível conectar-se ao banco de dados local '{self.database}': {e}", level=logging.ERROR)
        finally:
            if conn:
                conn.close()

    def log(self, message, srcaddr=None, level=logging.DEBUG):
        logging.log(level, f"<{srcaddr[0]}:{srcaddr[1]}> {message}" if srcaddr else message)

    def process_data(self, data, src):
        self.log(f"Recebido dados:\n{data}", src)

        if SMSServer.is_packet(data):
            packet = self.parse_packet(data)
            transaction_id = None
            transaction_valid = False

            try:
                transaction_id = int(packet.args[0])
            except ValueError:
                self.log(f"Esperado por valor inteiro representando o ID da transação, porém recebido: {packet.args[0]}.", src, level=logging.WARNING)
            except IndexError as e:
                self.log(f"Esperado por argumento junto ao packet, porém não recebido: : {e}.", src, level=logging.WARNING)

            if transaction_id:
                for transaction in self.pending_transactions:
                    if transaction.id == transaction_id and transaction.sms_client.srcaddr == src:
                        try:
                            transaction.set_current_response(packet)
                        except ValueError as e:
                            self.log(f"Esperado por argumento junto ao packet de um certo tipo, porém não recebido: : {e}.", src, level=logging.WARNING)

                        transaction.last_time = datetime.timestamp(datetime.now())
                        transaction_valid = True
                        break
            
            if not transaction_valid:
                self.log(f"Recebido packet da transação {packet.args[0]}, porém ela não existe.", src, level=logging.INFO)
        else:
            package = self.parse_package(data)

            # Tentativa de registro/keep-alive
            if 'req' in package:
                auth_client = None
                sms_client = None

                # ID existe?
                if package['id'] in self.auth_clients:
                    auth_client = self.auth_clients[package['id']]
                    sms_client = auth_client.authenticate(package['id'], package['pass'], src)
                    
                    if sms_client:
                        sms_client.update(package)
                
                if auth_client:
                    if sms_client:
                        self.log(f"Registrado heartbeat do ID '{package['id']}'.", src, level=logging.INFO)
                        sms_client.send_package(self.soc, {
                            "reg": package['req'],
                            "status": 200
                        })
                    else:
                        self.log(f"Falha na autenticação do ID '{package['id']}': Senha incorreta.", src, level=logging.INFO)
                        sms_client.send_package(self.soc, {
                            "reg": package['req'],
                            "status": 403
                        })
                else:
                    self.log(f"Falha na autenticação do ID '{package['id']}': Usuário inexistente.", src, level=logging.INFO)
                    sms_client.send_package(self.soc, {
                            "reg": package['req'],
                            "status": 403
                        })

    def process_pending_transactions(self, currsocket):
        for transaction in self.pending_transactions:
            if transaction.is_alive():
                currstep = transaction.get_current_step()

                if currstep:
                    self.process_transaction_step(currsocket, transaction, currstep)
                else:
                    # Nenhum pacote na transação?
                    self.log(f"Nenhum pacote na transação {transaction.id}, removendo.", srcaddr=transaction.sms_client.srcaddr, level=logging.INFO)
                    self.pending_transactions.remove(transaction)
            else:
                self.log(f"Transação {transaction.id} morta, timeout alcançado.'", srcaddr=transaction.sms_client.srcaddr, level=logging.INFO)
                self.pending_transactions.remove(transaction)

    def process_transaction_step(self, currsocket, transaction, currstep):
        if isinstance(currstep, SMSTransactionBundle):
            if currstep.valid_response():
                self.log(F"Finalizando SMSTransactionBundle: {currstep}", srcaddr=transaction.sms_client.srcaddr, level=logging.INFO)
                transaction.pop_current_step()
            else:
                previous = None

                for step in currstep.steps:
                    is_waiting = previous and (previous.is_waiting() or not previous.valid_response())

                    if not step.valid_response() and not is_waiting:
                        self.process_transaction_step(currsocket, transaction, step)
                    elif is_waiting:
                        break

                    previous = step
        else:
            if currstep.txpacket.state is SMSPacketState.SEND:
                transaction.sms_client.send_packet(currsocket, currstep.txpacket)
                
                self.log(F"Enviado packet para o cliente: TX: {currstep.txpacket.action} {' '.join(string_values(currstep.txpacket.args))}", srcaddr=transaction.sms_client.srcaddr)
            elif currstep.txpacket.state is SMSPacketState.SENT:
                if currstep.rxpacket:
                    # Verificar o que recebemos
                    received_packet = currstep.rxpacket[-1] if isinstance(currstep, SMSTransactionBundleStep) else currstep.rxpacket
                    response_is_valid = currstep.valid_response()

                    if received_packet.action == "ERROR" and not response_is_valid:
                        self.log(f"Recebido packet de ERROR na transação {transaction.id}, ignorando: TX: {currstep.txpacket.action} {' '.join(string_values(currstep.txpacket.args))}, RX: {received_packet.action} {' '.join(received_packet.args)}", srcaddr=transaction.sms_client.srcaddr)
                        #self.pending_transactions.remove(transaction)
                        transaction.pop_current_step()
                    else:
                        # if response_is_valid:
                        #     if received_packet.action == "WAIT":
                        #         self.log(f"Recebido packet de WAIT na transação {transaction.id}, esperando resposta: TX: {currstep.txpacket.action} {' '.join(string_values(currstep.txpacket.args))}, RX: {received_packet.action} {' '.join(received_packet.args)}", srcaddr=transaction.sms_client.srcaddr)
                        #         currstep.rxpacket = [] if isinstance(currstep, SMSTransactionBundleStep) else None
                        #     else:
                        #         if not isinstance(currstep, SMSTransactionBundleStep):
                        #             transaction.pop_current_step()
                        #         else:
                        #             # Sou um BundleStep, só posso finalizar o Bundle, quando todos os pacotes forem finalizados, e quem gerência essa finalização é o Bundle e não o próprio Step.
                        #             pass
                        # else:
                        #     if not isinstance(currstep, SMSTransactionBundleStep):
                        #         self.log(f"Resposta não aprovada na transação {transaction.id}, cancelando transação: TX: {currstep.txpacket.action} {' '.join(string_values(currstep.txpacket.args))}, RX: {received_packet.action} {' '.join(received_packet.args)}", srcaddr=transaction.sms_client.srcaddr)
                        #         self.pending_transactions.remove(transaction)

                        if received_packet.action != "WAIT":
                            if response_is_valid:
                                if not isinstance(currstep, SMSTransactionBundleStep):
                                    # Ok, somos um passo normal, recebemos o que esperávamos, passe para o próximo...
                                    transaction.pop_current_step()
                                else:
                                    # Somos um SMSTransactionBundleStep, não podemos avançar por sí mesmo, quem fará isso é o próprio SMSTransactionBundle
                                    pass
                            else:
                                #if not isinstance(currstep, SMSTransactionBundleStep):
                                self.log(f"Resposta não aprovada na transação {transaction.id}, cancelando transação: TX: {currstep.txpacket.action} {' '.join(string_values(currstep.txpacket.args))}, RX: {received_packet.action} {' '.join(received_packet.args)}", srcaddr=transaction.sms_client.srcaddr)
                                self.pending_transactions.remove(transaction)

    def listen(self):
        self.log(f"Escutando em '{self.bindaddr}', porta {self.port}...", level=logging.INFO)

        self.soc = socket.socket(
            socket.AF_INET,         # IPV4
            socket.SOCK_DGRAM        # UDP
        )
        
        self.soc.bind(
            (self.bindaddr, self.port)
        )

        consecutive_packages = 0

        try:
            while True:
                # Previne que apena seja possível responder caso dados novos cheguem
                # Permitindo efetuar tarefas enquanto estamos esperando por mais pedidos
                if consecutive_packages < self.max_consecutive_packages and self.soc in select.select([self.soc], [], [], 0)[0]:
                    consecutive_packages += 1

                    # Bloqueia a execução caso nao tenha nada a ser lido.
                    data, src = self.soc.recvfrom(self.buffersize)
                
                    try:
                        data = data.decode('utf-8')
                    
                        if data:
                            self.process_data(data, src)

                    except UnicodeDecodeError as e:
                        self.log(f"Nao foi possível decodificar os dados recebidos, unicode esperado: {e}", src, level=logging.WARNING)
                    except KeyError as e:
                        self.log(f"Esperado chave do cliente, porém inexistente: {e}", src, level=logging.WARNING)
                    except SMSServerParseError as e:
                        self.log(f"Falha ao efetuar parse_package/parse_packet: {e}", src, level=logging.WARNING)
                else:
                    consecutive_packages = 0

                    self.process_pending_transactions(self.soc)
                    
                    time.sleep(.1)

        except KeyboardInterrupt:
            self.log(f"Desligando socket", level=logging.INFO)
            self.stop()

    def stop(self):
        self.soc.close()

    def begin_transaction_for(self, sms_client, action, timeout=60):
        self.log(f"Iniciando transação {action.id} para {type(action).__name__}, message='{action.message}', numbers={action.numbers}", srcaddr=sms_client.srcaddr)

        transaction = SMSTransaction(action.id, sms_client, self.generate_steps_for(action), timeout=timeout)
        self.pending_transactions.append(transaction)

    @staticmethod
    def is_packet(data):
        action = ""

        for c in data:
            if c == ' ':
                break

            action += c

        return action in (
            "PASSWORD",
            "SEND",
            "OK",
            "WAIT",
            "DONE",
            "ERROR"
        )

    @staticmethod
    def generate_steps_for(action):
        if isinstance(action, SendSMSAction):
            def callback_all_sends_finished(steps):
                num_finished = 0

                for step in steps.steps:
                    if step.txpacket.state is SMSPacketState.SENT and step.rxpacket and step.valid_response():
                        num_finished += 1

                return num_finished == len(steps.steps)

            steps = [
                SMSTransactionStep(
                    SMSPacket("MSG", args=[
                            action.id, 
                            len(action.message.encode('utf-8')), 
                            action.message
                        ]
                    ), lambda x: x.action == "PASSWORD"),
                SMSTransactionStep(
                    SMSPacket("PASSWORD", args=[
                            action.id, 
                            action.sms_client.auth_client.password
                        ]
                    ), lambda x: x.action == "SEND"),
                SMSTransactionBundle(
                    [
                        SMSTransactionBundleStep(
                            SMSPacket("SEND", args=[
                                    action.id, 
                                    i + 1, 
                                    action.numbers[i]
                                ]
                            ), 
                            lambda x: x.action == "OK" or x.action == "ERROR"
                        ) for i in range(len(action.numbers))
                    ],
                    callback_all_sends_finished
                ),
                SMSTransactionStep(
                    SMSPacket("DONE", args=[
                            action.id
                        ]
                    ), 
                    lambda x: x.action == "DONE"
                )
            ]
        else:
            raise ValueError("Não é possível gerar os passos de uma action desconhecida.")

        return steps

    @staticmethod
    def parse_packet(data):
        split = data.split(" ")

        if len(split) > 1:
            return SMSPacket(
                split[0], 
                args=split[1:], 
                state=SMSPacketState.RECEIVED
            )
        else:
            raise SMSServerParseError("Recebido packet incompleto, faltando argumentos.")

    @staticmethod
    def parse_package(data):
        ret = {}

        kvs = data.split(';')
        if len(kvs) > 1:
            for kv in kvs:
                split = kv.split(":")

                if split:
                    ret[split[0]] = ":".join(split[1:]) if len(split) > 1 else ""
        else:
            raise SMSServerParseError("Cojunto de chaves e valores está incorreto e/ou incompleto.")

        return ret

    @staticmethod
    def create_package(kwvalues):
        return ";".join(
            [f"{key}:{value}" for key, value in kwvalues.items()]
        )