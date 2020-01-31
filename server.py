#!/usr/bin/python3

import socket
from datetime import datetime

class SMSAuthClient:
	def __init__(self, username, password):
		self.username = username
		self.password = password
		self.connected = []

	def authenticate(self, username, password, srcaddr):
		if username != self.username or password != self.password:
			return False

		c = self.get_client_from(srcaddr)

		if c:
			c.heartbeat(srcaddr)
		else:
			self.connected.append(SMSClient(
				srcaddr
			))

		return True

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

	def listen(self):
		print(f"Escutando em {self.bindaddr}, porta {self.port}...")

		self.soc = socket.socket(
			socket.AF_INET, 		# IPV4
			socket.SOCK_DGRAM		# UDP
		)
		
		self.soc.bind(
			(self.bindaddr, self.port)
		)

		try:
			while True:
				# BLoqueia a execução caso nao tenha nada a ser lido.
				data, src = self.soc.recvfrom(self.buffersize)
				
				try:
					data = data.decode('utf-8')
				except Exception:
					data = ""
				
				if data:
					print(f"[{datetime.now()}] Recebido dados de [{src[0]}:{src[1]}]:\n{data}")

					package = None
					
					try:
						package = self.read_package(data)
					except ValueError as e:
						print(f"Falha ao construir pacote de valores: {e}")

					if package:
						if 'req' in package and 'id' in package and 'pass' in package:
							# Auth
							if package['id'] in self.auth_clients:
								c = self.auth_clients[package['id']]

								if c.authenticate(package['id'], package['pass'], src):
									print(f"[{datetime.now()}] Registrado '{package['id']}' em [{src[0]}:{src[1]}].")
								else:
									print(f"[{datetime.now()}] Falha na autenticação [{src[0]}:{src[1]}]: Senha incorreta.")
							else:
								print(f"[{datetime.now()}] Falha na autenticação [{src[0]}:{src[1]}]: Usuário inexistente.")
						elif 'id' in package and 'password'

		except KeyboardInterrupt:
			self.stop()

	def stop(self):
		self.soc.close()

	def read_package(self, data):
		ret = {}

		kvs = data.split(';')
		if len(kvs) > 1:
			for kv in kvs:
				split = kv.split(":")

				if split:
					ret[split[0]] = ":".join(split[1:]) if len(split) > 1 else ""
		else:
			raise ValueError("Cojunto de chaves e valores está incorreto e/ou incompleto.")

		return ret

if __name__ == '__main__':
	SMSServer('192.168.1.107', 44444).listen()
