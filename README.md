# Bot Ponto RD2 - Discord Time Tracking Bot

Este é um bot do Discord desenvolvido para gerenciar registros de ponto (entrada/saída) para um RP no Red Dead Redemption 2.

## Funcionalidades

- Registro de entrada (!entrada)
- Registro de saída (!saida)
- Geração de relatório de horas (!relatorio [dias])

## Configuração

1. Instale as dependências:
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

- `!entrada` - Registra o horário de entrada
- `!saida` - Registra o horário de saída
- `!relatorio [dias]` - Gera um relatório das horas trabalhadas nos últimos X dias (padrão: 7 dias)

## Notas

- O bot utiliza SQLite para armazenar os registros de ponto
- Os horários são registrados no momento em que os comandos são executados
- O relatório mostra as entradas, saídas e o total de horas no período
