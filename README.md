# Bot Ponto RD2 - Discord Time Tracking Bot

Este � um bot do Discord desenvolvido para gerenciar registros de ponto (entrada/sa�da) para um RP no Red Dead Redemption 2.

## Funcionalidades

- Registro de entrada (!entrada)
- Registro de sa�da (!saida)
- Gera��o de relat�rio de horas (!relatorio [dias])

## Configura��o

1. Instale as depend�ncias:
```bash
pip install -r requirements.txt
```

2. Configure o arquivo `.env`:
- Copie o arquivo `.env` e substitua `your_token_here` pelo token do seu bot Discord

3. Execute o bot:
```bash
python src/bot.py
```

## Comandos

- `!entrada` - Registra o hor�rio de entrada
- `!saida` - Registra o hor�rio de sa�da
- `!relatorio [dias]` - Gera um relat�rio das horas trabalhadas nos �ltimos X dias (padr�o: 7 dias)

## Notas

- O bot utiliza SQLite para armazenar os registros de ponto
- Os hor�rios s�o registrados no momento em que os comandos s�o executados
- O relat�rio mostra as entradas, sa�das e o total de horas no per�odo
