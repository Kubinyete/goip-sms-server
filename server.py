#!/usr/bin/python3

import socket
import sys
import select
from datetime import datetime

class SMSServerParseError(Exception):
	pass

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
			self.connected.append(SMSClient(
				srcaddr
			))

		return c

	def get_client_from(self, srcaddr):
		for c in self.connected:
			if c.srcaddr == srcaddr:
				return c

		return None

class SMSClient:
	def __init__(self, srcaddr, expires=60):
		self.srcaddr = srcaddr
		self.last_heartbeat = datetime.timestamp(datetime.now())
		self.expires = expires

	def is_alive(self):
		return  self.last_heartbeat + self.expires > datetime.timestamp(datetime.now())

	def heartbeat(self, srcaddr=None):
		self.last_heartbeat = datetime.timestamp(datetime.now())
		
		if srcaddr:
			self.srcaddr = srcaddr

	def send_package(self, currsocket, package):
		packagestr = SMSServer.create_package(package)
		currsocket.sendto(packagestr.encode('utf-8'), socket.MSG_DONTWAIT, self.srcaddr)

class SMSServer:
	def __init__(self, bindaddr, port, buffersize=2048):
		self.bindaddr = bindaddr
		self.port = port
		self.soc = None
		self.buffersize = buffersize
		self.auth_clients = {}

		self.fetch_authorized_clients()

	def fetch_authorized_clients(self):
		# @TODO: Pegar as contas de autenticação externamente, isso atualmente é temporário para testes
		self.auth_clients = {
			"centralvox": SMSAuthClient('centralvox', 'centralvox')
		}

	def log(self, message, srcaddr=None):
		timestamp = datetime.now()

		print(f"[{timestamp}] {message}" if srcaddr is None else f"[{timestamp}] <{srcaddr[0]}:{srcaddr[1]}> {message}")

	def listen(self):
		print(f"Escutando em '{self.bindaddr}', porta {self.port}...")

		self.soc = socket.socket(
			socket.AF_INET, 		# IPV4
			socket.SOCK_DGRAM		# UDP
		)
		
		self.soc.bind(
			(self.bindaddr, self.port)
		)

		try:
			while True:
				if self.soc in select.select([self.soc], [], [], 0)[0]:
					# Bloqueia a execução caso nao tenha nada a ser lido.
					data, src = self.soc.recvfrom(self.buffersize)
				
					try:
						data = data.decode('utf-8')
					
						if data:
							self.log(f"Recebido dados:\n{data}", src)

							package = self.parse_package(data)

							# Tentativa de registro/keep-alive
							if 'req' in package:
								auth_client = None
								sms_client = None

								# ID existe?
								if package['id'] in self.auth_clients:
									auth_client = self.auth_clients[package['id']]
									sms_client = auth_client.authenticate(package['id'], package['pass'], src)
								
								if auth_client:
									if sms_client:
										self.log(f"Registrado heartbeat do ID '{package['id']}'.", src)
										
										sms_client.send_package(self.soc, {
											"reg": package['req'],
											"status": 200
										})
									else:
										self.log(f"Falha na autenticação do ID '{package['id']}': Senha incorreta.", src)
								else:
									self.log(f"Falha na autenticação do ID '{package['id']}': Usuário inexistente.", src)
					except UnicodeDecodeError as e:
						self.log(f"Nao foi possível decodificar os dados recebidos, unicode esperado: {e}", src)
					except KeyError as e:
						self.log(f"Esperado chave do cliente, porém inexistente: {e}", src)
					except SMSServerParseError as e:
						self.log(f"Falha ao efetuar parse_package: {e}", src)
				else:
					# Verificar se existem solicitações de SMS a serem enviadas
					pass
		except KeyboardInterrupt:
			self.stop()

	def stop(self):
		self.soc.close()

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
