import asyncio
import json

import websockets

j = {
    'e': 'kline',
    'E': 1688913452159,
    's': 'BTCUSDT',
    'k':
        {
            't': 1688913420000,
            'T': 1688913479999,
            's': 'BTCUSDT',
            'i': '1m',
            'f': 3167074357,
            'L': 3167075282,
            'o': '30422.02000000',
            'c': '30418.73000000',
            'h': '30428.93000000',
            'l': '30390.26000000',
            'v': '31.93272000',
            'n': 926,
            'x': False,
            'q': '971168.82602050',
            'V': '14.30575000',
            'Q': '435001.37073690',
            'B': '0'
        }
    }

async def main():
    async with websockets.connect(f'wss://data-stream.binance.vision:443/ws/btcusdt@kline_1s') as ws:
        while 1:
            response = await ws.recv()
            print(response)

asyncio.run(main())