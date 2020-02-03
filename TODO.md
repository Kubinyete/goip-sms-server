# TODO

* Implementação de log com a utilização de arquivos aon invés de jogar na STDOUT (causa problemas e o STDOUT continua ligado ao terminal que iniciou o daemon)
* Utilização de mais de uma thread (verificar possiblidade, efetuar alterações necessárias)

## Lembretes

* Não sei exatamente se a forma com que efetuamos parse nos pacotes é eficiente (apenas especificar um buffer grande e não se preocupar com o conteudo ou tamanho dos pacotes), pode ser que cause problemas no futuro.