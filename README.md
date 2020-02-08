# Simple SMS Server

Primeiro projeto em sockets com o objetivo de criar um mini-servidor (singlethreaded por enquanto) de SMS para comunicação com a interface de SMS dos GoIPs, para mais informações sobre o funcionamento dessa interface veja um outro projeto [FSG-GOIP-snipper](https://github.com/iivorait/FSG-GOIP-snippet) e a documentação da interface [aqui](https://github.com/iivorait/FSG-GOIP-snippet/blob/master/doc/goip_sms_Interface_en.pdf).

## Sobre

A comunicação, feita sobre UDP, acontece através de pequenas "transações" entre as duas pontas, já que não estamos utilizando TCP. Como este é meu primeiro projeto de servidor, resolvi adotar uma maneira mais simples de estruturar as transações, com o conceito de etapas e verificação de cada uma delas, portanto, fica registrado em memória um "mapa da transação" e todos os pacotes serem enviados, cada etapa portanto pode ter um pacote resposta (ou vários caso necessário).

## Aviso

Esse servidor foi construido para fins de aprendizado, nenhum dos componentes oferecidos foi rigorosamente testado e portanto poderá apresentar falhas.

## Requerimentos

* daemons - Biblioteca para criação de Daemons.
* sqlite3 - Já instalado juntamente com sua versão do Python
* Python 3 (3.7 ou mais recente é recomendado)

## Uso

```console
vitor@ubuntu $ ./launcher.py
Uso:

./launcher.py <start|stop|restart|sync|db|sms> [arg1] [arg2]...
```

Para começar será necessário inicializar o banco de dados SQLite local:

```console
vitor@ubuntu $ ./launcher.py db create
```

Feito isso, será preciso adicionar pelo menos um usuário para autenticação:

```console
vitor@ubuntu $ ./launcher.py db useradd meuusuario minhasenha
```

Após iniciarmos o servidor e a linha se registrar com o servidor, será possível registrar um pedido de envio de SMS:

```console
vitor@ubuntu $ ./launcher.py sms send meuusuario "texto" 18999993333 11999993334
```
