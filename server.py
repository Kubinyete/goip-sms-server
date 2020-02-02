#!/usr/bin/python3

import socket
import sys
import select
from datetime import datetime
from enum import Enum, auto


class SMSPacketState(Enum):
	SEND = auto()
	SENT = auto()
	RECEIVED = auto()

class SMSServerParseError(Exception):
	pass

class SMSPacket:
	def __init__(self, action, args=[], state=SMSPacketState.SEND):
		self.action = action
		self.args = args
		self.state = state

	def generate(self):
		return self.action.encode('utf-8') + b" " + " ".join([str(a) for a in self.args]).encode('utf-8') + b"\n"

class SMSTransactionStep:
	def __init__(self, txpacket, rxpacket=None, expected=lambda x: x.action != "ERROR"):
		self.txpacket = txpacket
		self.rxpacket = rxpacket
		self.excepted = expected

	def valid_response(self):
		return self.rxpacket and self.excepted(self.rxpacket)

class SMSTransaction:
	def __init__(self, id, sms_client, steps, timeout=60):
		self.id = id
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

		if currstep and not currstep.rxpacket:
			currstep.rxpacket = rxpacket

	def is_alive(self):
		return self.last_time + self.timeout > datetime.timestamp(datetime.now())

class SMSAction:
	AUTO_INCREMENT = 0

	def __init__(self, id, sms_client):
		self.id = id
		self.sms_client = sms_client

	@staticmethod
	def generate_id():
		if SMSAction.AUTO_INCREMENT == sys.maxsize:
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
		self.signal = int(package.get('signal', 0))
		self.gsm_status = package.get('gsm_status', '')
		self.voip_status = package.get('voip_status', '')
		self.voip_state = package.get('voip_state', '')
		self.remain_time = int(package.get('remain_time', -1))
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
	def __init__(self, bindaddr, port, buffersize=2048, max_consecutive_packages=3):
		self.bindaddr = bindaddr
		self.port = port
		self.soc = None
		self.buffersize = buffersize
		self.auth_clients = {}
		self.pending_transactions = []
		self.max_consecutive_packages = max_consecutive_packages

		self.fetch_authorized_clients()

	def fetch_authorized_clients(self):
		# @TODO: Pegar as contas de autenticação externamente, isso atualmente é temporário para testes
		self.auth_clients = {
			"centralvox": SMSAuthClient('centralvox', 'centralvox')
		}

	def log(self, message, srcaddr=None):
		timestamp = datetime.now()

		print(f"[{timestamp}] {message}" if srcaddr is None else f"[{timestamp}] <{srcaddr[0]}:{srcaddr[1]}> {message}")

	def process_data(self, data, src):
		self.log(f"Recebido dados:\n{data}", src)

		if SMSServer.is_packet(data):
			packet = self.parse_packet(data)
			transaction_id = packet.args[0]

			for transaction in self.pending_transactions:
				if transaction.id == transaction_id and transaction.sms_cliemt.srcaddr == src:
					transaction.set_current_response(packet)
					transaction.last_time = datetime.timestamp(datetime.now())
			else:
				self.log(f"Recebido packet da transação {transaction_id}, porém ela não existe.", src)
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
						self.log(f"Registrado heartbeat do ID '{package['id']}'.", src)
						sms_client.send_package(self.soc, {
							"reg": package['req'],
							"status": 200
						})
					else:
						self.log(f"Falha na autenticação do ID '{package['id']}': Senha incorreta.", src)
						sms_client.send_package(self.soc, {
							"reg": package['req'],
							"status": 403
						})
				else:
					self.log(f"Falha na autenticação do ID '{package['id']}': Usuário inexistente.", src)
					sms_client.send_package(self.soc, {
							"reg": package['req'],
							"status": 403
						})

	def process_pending_transactions(self, currsocket):
		for transaction in self.pending_transactions:
			if transaction.is_alive():
				currstep = transaction.get_current_step()

				if currstep and currstep.txpacket:
					if currstep.txpacket.state is SMSPacketState.SEND:
						transaction.sms_client.send_packet(currsocket, currstep.txpacket)
					elif currstep.txpacket.state is SMSPacketState.SENT:
						if currstep.rxpacket:
							# Verificar o que recebemos
							received_packet = currstep.rxpacket

							if received_packet.action == "ERROR":
								self.log(f"Recebido packet de ERROR na transação {transaction.id}, cancelando transação: {received_packet.action} {' '.join(received_packet.args)}", srcaddr=transaction.sms_client.srcaddr)
								self.pending_transactions.remove(transaction)
							else:
								if currstep.valid_response():
									if received_packet.action == "WAIT":
										self.log(f"Recebido packet de WAIT na transação {transaction.id}, esperando resposta: {received_packet.action} {' '.join(received_packet.args)}", srcaddr=transaction.sms_client.srcaddr)
										currstep.rxpacket = None
									else:
										transaction.pop_current_step()
								else:
									self.log(f"Resposta não aprovada na transação {transaction.id}, cancelando transação: TX: {currstep.txpacket.action} {' '.join(currstep.txpacket.args)}RX: {received_packet.action} {' '.join(received_packet.args)}", srcaddr=transaction.sms_client.srcaddr)
									self.pending_transactions.remove(transaction)
				else:
					# Nenhum pacote na transação?
					self.log(f"Nenhum pacote na transação {transaction.id}, removendo.", srcaddr=transaction.sms_client.srcaddr)
			else:
				self.log(f"Transação {transaction.id} morta, timeout alcançado.'", srcaddr=transaction.sms_client.srcaddr)
				self.pending_transactions.remove(transaction)

	def listen(self):
		self.log(f"Escutando em '{self.bindaddr}', porta {self.port}...")

		self.soc = socket.socket(
			socket.AF_INET, 		# IPV4
			socket.SOCK_DGRAM		# UDP
		)
		
		self.soc.bind(
			(self.bindaddr, self.port)
		)

		consecutive_packages = 0
		debug = True

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
						self.log(f"Nao foi possível decodificar os dados recebidos, unicode esperado: {e}", src)
					except KeyError as e:
						self.log(f"Esperado chave do cliente, porém inexistente: {e}", src)
					except SMSServerParseError as e:
						self.log(f"Falha ao efetuar parse_package/parse_packet: {e}", src)
				else:
					consecutive_packages = 0

					# Verificar se existem solicitações de SMS a serem enviadas
					# @DEBUG: Apenas para teste
					
					self.process_pending_transactions(self.soc)

					if debug and self.auth_clients['centralvox'].connected:
						debug = False

						auth_client = self.auth_clients['centralvox']
						for sms_client in auth_client.connected:
							if sms_client.is_alive():
								self.begin_transaction_for(sms_client, SMSAction.get_sendsms_action(sms_client, "Teste", [11111111111]))

		except KeyboardInterrupt:
			self.log(f"Desligando socket...")
			self.stop()

	def stop(self):
		self.soc.close()

	def begin_transaction_for(self, sms_cliemt, action, timeout=60):
		transaction = SMSTransaction(action.id, sms_cliemt, self.generate_steps_for(action), timeout=timeout)
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
			"DONE"
		)

	@staticmethod
	def generate_steps_for(action):
		if isinstance(action, SendSMSAction):
			steps = [
				SMSTransactionStep(SMSPacket("MSG", args=[action.id, len(action.message.encode('utf-8')), action.message]), None, lambda x: x.action == "PASSWORD"),
				SMSTransactionStep(SMSPacket("PASSWORD", args=[action.id, action.sms_client.auth_client.password]), None, lambda x: x.action == "SEND"),
			]
			
			for i in range(len(action.numbers)):
				steps.append(
					SMSTransactionStep(SMSPacket("SEND", args=[action.id, i, action.numbers[i]]), None, expected=lambda x: x.action in ("OK", "WAIT"))
				)

			steps.append(
				SMSTransactionStep((SMSPacket("DONE", args=[action.id]), None, lambda x: x.action == "DONE"))
			)
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

if __name__ == '__main__':
	SMSServer('', 44444).listen()
