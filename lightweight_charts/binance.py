import asyncio
import logging
import datetime as dt
import threading
import queue
import json
import ssl
from typing import Literal, Union, List
import pandas as pd

from lightweight_charts.util import _convert_timeframe
from lightweight_charts import Chart

try:
    import requests
except ImportError:
    requests = None
try:
    import websockets
except ImportError:
    websockets = None


class BinanceAPI:
    """
    Offers direct access to Polygon API data within all Chart objects.

    It is not designed to be initialized by the user, and should be utilised
    through the `polygon` method of `LWC` (chart.polygon.<method>).
    """
    def __init__(self, chart):
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter('%(asctime)s | [polygon.io] %(levelname)s: %(message)s', datefmt='%H:%M:%S'))
        ch.setLevel(logging.DEBUG)
        self._log = logging.getLogger('binance')
        self._log.setLevel(logging.ERROR)
        self._log.addHandler(ch)

        self.max_ticks_per_response = 20

        self._chart = chart
        self._lasts = {}
        self._key = None

        self._ws_q = queue.Queue()
        self._q = queue.Queue()
        self._lock = threading.Lock()

        self._using_live_data = False
        self._using_live = {'crypto': True}
        self._ws = {'crypto': None}

    def log(self, info: bool):
        """
        Streams informational messages related to Polygon.io.
        """
        self._log.setLevel(logging.INFO) if info else self._log.setLevel(logging.ERROR)

    # 现货
    def spot(self, crypto_pair, timeframe, start_date, end_date='now', limit: int = 5_00, live=False):
        """
        Requests and displays crypto data pulled from Polygon.io.\n
        :param crypto_pair: The crypto pair to request. (BTC-USD, ETH-BTC etc.)
        :param timeframe:   Timeframe to request (1min, 5min, 2H, 1D, 1W, 2M, etc).
        :param start_date:  Start date of the data (YYYY-MM-DD).
        :param end_date:    End date of the data (YYYY-MM-DD). If left blank, this will be set to today.
        :param limit:       The limit of base aggregates queried to create the timeframe given (max 50_000).
        :param live:        If true, the data will be updated in real-time.
        """
        return self._set(self._chart, 'crypto', f'{crypto_pair}'.replace('-', '').replace('/', '').upper(), "1m", start_date, end_date, limit, live)

    # 合约
    def future(self):
        pass

    def stock(self, symbol: str, timeframe: str, start_date: str, end_date='now', limit: int = 5_000, live: bool = False):
        """
        Requests and displays stock data pulled from Polygon.io.\n
        :param symbol:      Ticker to request.
        :param timeframe:   Timeframe to request (1min, 5min, 2H, 1D, 1W, 2M, etc).
        :param start_date:  Start date of the data (YYYY-MM-DD).
        :param end_date:    End date of the data (YYYY-MM-DD). If left blank, this will be set to today.
        :param limit:       The limit of base aggregates queried to create the timeframe given (max 50_000).
        :param live:        If true, the data will be updated in real-time.
        """
        return self._set(self._chart, 'stocks', symbol, timeframe, start_date, end_date, limit, live)

    def crypto(self, crypto_pair, timeframe, start_date, end_date='now', limit: int = 5_000, live=False):
        """
        Requests and displays crypto data pulled from Polygon.io.\n
        :param crypto_pair: The crypto pair to request. (BTC-USD, ETH-BTC etc.)
        :param timeframe:   Timeframe to request (1min, 5min, 2H, 1D, 1W, 2M, etc).
        :param start_date:  Start date of the data (YYYY-MM-DD).
        :param end_date:    End date of the data (YYYY-MM-DD). If left blank, this will be set to today.
        :param limit:       The limit of base aggregates queried to create the timeframe given (max 50_000).
        :param live:        If true, the data will be updated in real-time.
        """
        return self._set(self._chart, 'crypto', f'X:{crypto_pair}', timeframe, start_date, end_date, limit, live)

    def _set(self, chart, sec_type, ticker, timeframe, start_date, end_date, limit, live):
        if requests is None:
            raise ImportError('The "requests" library was not found, and must be installed to use polygon.io.')

        self._ws_q.put(('_unsubscribe', chart))

        start_date_ts = int(dt.datetime.strptime(start_date, '%Y-%m-%d').timestamp() * 1000)
        end_date_ts = int(dt.datetime.now().timestamp()*1000) if end_date == 'now' else int(dt.datetime.strptime(end_date, '%Y-%m-%d').timestamp() * 1000)

        # end_date = dt.datetime.now().strftime('%Y-%m-%d') if end_date == 'now' else end_date
        # mult, span = _convert_timeframe(timeframe)

        # query_url = f"https://data-api.binance.vision/api/v3/klines?symbol={ticker}&interval={timeframe}&startTime={start_date_ts}&endTime={end_date_ts}&limit={limit}"
        query_url = f"https://data-api.binance.vision/api/v3/klines?symbol={ticker}&interval={timeframe}&limit={limit}"
        response = requests.get(query_url)
        if response.status_code != 200:
            error = response.json()
            self._log.error(f'({response.status_code}) Request failed: {error["error"]}')
            return
        data = response.json()
        if len(data) <= 0:
            self._log.error(f'No results for "{ticker}" ({sec_type})')
            return

        df = pd.DataFrame(data).loc[:, [0, 1, 2, 3, 4, 5]]
        columns = ['time', 'open', 'high', 'low', 'close', 'volume']
        df.columns = columns
        df['time'] = pd.to_datetime(df['time'], unit='ms')
        chart.set(df)

        if not live:
            return True
        if not self._using_live_data:
            threading.Thread(target=asyncio.run, args=[self._thread_loop()], daemon=True).start()
            self._using_live_data = True
        with self._lock:
            if not self._ws[sec_type]:
                self._ws_q.put(('_websocket_connect', self._key, sec_type))
        self._ws_q.put(('_subscribe', chart, ticker, sec_type))
        return True

    async def _thread_loop(self):
        while 1:
            while self._ws_q.empty():
                await asyncio.sleep(0.05)
            value = self._ws_q.get()
            func, args = value[0], value[1:]
            asyncio.create_task(getattr(self, func)(*args))

    async def _subscribe(self, chart, ticker, sec_type):
        key = ticker
        if not self._lasts.get(key):
            self._lasts[key] = {
                'ticker': ticker,
                'sec_type': sec_type,  # crypto
                'sub_type': ('XQ', 'XA'),
                'price': chart._last_bar['close'],
                'charts': [],
            }
        # quotes, aggs = self._lasts[key]['sub_type']
        await self._send(self._lasts[key]['sec_type'], 'SUBSCRIBE', ["btcusdt@aggTrade", "btcusdt@depth"])
        # await self._send(self._lasts[key]['sec_type'], 'subscribe', f'{aggs}.{ticker}') if aggs else None

        self._lasts[key]['volume'] = chart._last_bar['volume']
        if chart in self._lasts[key]['charts']:
            return
        self._lasts[key]['charts'].append(chart)

    async def _unsubscribe(self, chart):
        for data in self._lasts.values():
            if chart in data['charts']:
                break
        else:
            return
        if chart in data['charts']:
            data['charts'].remove(chart)
        if data['charts']:
            return

        while self._q.qsize():
            self._q.get()  # Flush the queue
        quotes, aggs = data['sub_type']
        await self._send(data['sec_type'], 'UNSUBSCRIBE', ["btcusdt@aggTrade", "btcusdt@kline_1m"])
        # await self._send(data['sec_type'], 'unsubscribe', f'{aggs}.{data["ticker"]}')

    async def _send(self, sec_type, action, params: list):
        while 1:
            with self._lock:
                ws = self._ws[sec_type]
            if ws:
                break
            await asyncio.sleep(0.1)
        await ws.send(json.dumps({'method': action, 'params': params, 'id': 10}))

    async def _handle_tick(self, sec_type, data):  # 暂时拿aggTrade测试
        key = data['s']  # symbol
        data['t'] = pd.to_datetime(data['T'], unit='ms')

        # if data['ev'] in ('Q', 'V', 'C', 'XQ'):  # xq quotes数据 对应 bookTicker? miniTicker？ 暂时用aggTrade
        #     self._lasts[key]['time'] = data['t']
        #     self._lasts[key]['price'] = (data['bp']+data['ap'])/2 if sec_type != 'indices' else data['val']
        #     self._lasts[key]['volume'] = 0
        # elif data['ev'] in ('A', 'CA', 'XA'):  # xa agg数据 对应 kline？
        #     self._lasts[key]['volume'] = data['v']
        #     if not self._lasts[key].get('time'):
        #         return

        self._lasts[key]['time'] = data['t']
        self._lasts[key]['price'] = data['p']
        self._lasts[key]['volume'] = 100

        for chart in self._lasts[key]['charts']:
            # self._q.put((chart.update_from_tick, pd.Series(self._lasts[key]), True))
            chart.update_from_tick(pd.Series(self._lasts[key]))

    async def _websocket_connect(self, api_key, sec_type):
        if websockets is None:
            raise ImportError('The "websockets" library was not found, and must be installed to pull live data.')
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        async with websockets.connect(f'wss://data-stream.binance.vision:443/ws') as ws:
            with self._lock:
                self._ws[sec_type] = ws
            # await self._send(sec_type, 'auth', api_key)
            while 1:
                response = await ws.recv()
                data: dict = json.loads(response)
                # print(data)
                if 'e' not in data:
                    continue
                if data['e'] in ['kline', 'aggTrade']:  # 当前只处理这两个事件
                    await self._handle_tick(sec_type, data)

    def _subchart(self, subchart):
        return BinanceAPISubChart(self, subchart)


class BinanceAPISubChart(BinanceAPI):
    def __init__(self, binance, subchart):
        super().__init__(subchart)
        self._set = binance._set


class BinanceChart(Chart):
    """
    A prebuilt callback chart object allowing for a standalone and plug-and-play
    experience of Polygon.io's API.

    Tickers, security types and timeframes are to be defined within the chart window.

    If using the standard `show` method, the `block` parameter must be set to True.
    When using `show_async`, either is acceptable.
    """
    def __init__(self, live: bool = False, num_bars: int = 200, end_date: str = 'now', limit: int = 5_000,
                 timeframe_options: tuple = ('1min', '5min', '30min', 'D', 'W'),
                 security_options: tuple = ('Stock', 'Option', 'Index', 'Forex', 'Crypto'),
                 width: int = 800, height: int = 600, x: int = None, y: int = None,
                 on_top: bool = False, maximize: bool = False, debug: bool = False):
        super().__init__(volume_enabled=True, width=width, height=height, x=x, y=y, on_top=on_top, maximize=maximize, debug=debug,
                         api=self, topbar=True, searchbox=True)
        self.chart = self
        self.num_bars = num_bars
        self.end_date = end_date
        self.limit = limit
        self.live = live

        self.topbar.active_background_color = 'rgb(91, 98, 246)'
        self.topbar.textbox('symbol')
        self.topbar.switcher('timeframe', self._on_timeframe_selection, *timeframe_options)
        self.topbar.switcher('security', self._on_security_selection, *security_options)
        self.legend(True)
        self.grid(False, False)
        self.crosshair(vert_visible=False, horz_visible=False)
        self.run_script(f'''
        {self.id}.search.box.style.backgroundColor = 'rgba(91, 98, 246, 0.5)'
        {self.id}.spinner.style.borderTop = '4px solid rgba(91, 98, 246, 0.8)'

        {self.id}.search.window.style.display = "flex"
        {self.id}.search.box.focus()
        
        //let polyLogo = document.createElement('div')
        //polyLogo.innerHTML = '<svg><g transform="scale(0.9)"><path d="M17.9821362,6 L24,12.1195009 L22.9236698,13.5060353 L17.9524621,27 L14.9907916,17.5798557 L12,12.0454987 L17.9821362,6 Z M21.437,15.304 L18.3670383,19.1065035 L18.367,23.637 L21.437,15.304 Z M18.203,7.335 L15.763,17.462 L17.595,23.287 L17.5955435,18.8249858 L22.963,12.176 L18.203,7.335 Z M17.297,7.799 L12.9564162,12.1857947 L15.228,16.389 L17.297,7.799 Z" fill="#FFFFFF"></path></g></svg>'
        //polyLogo.style.position = 'absolute'
        //polyLogo.style.width = '28px'
        //polyLogo.style.zIndex = 10000
        //polyLogo.style.right = '18px'
        //polyLogo.style.top = '-1px'
        //{self.id}.wrapper.appendChild(polyLogo)
        ''')

    def _binance(self, symbol):
        self.spinner(True)
        self.set(pd.DataFrame())
        self.crosshair(vert_visible=False, horz_visible=False)

        mult, span = _convert_timeframe(self.topbar['timeframe'].value)
        delta = dt.timedelta(**{span + 's': int(mult)})
        short_delta = (delta < dt.timedelta(days=7))
        start_date = dt.datetime.now() if self.end_date == 'now' else dt.datetime.strptime(self.end_date, '%Y-%m-%d')
        remaining_bars = self.num_bars
        while remaining_bars > 0:
            start_date -= delta
            if start_date.weekday() > 4 and short_delta:  # Monday to Friday (0 to 4)
                continue
            remaining_bars -= 1
        epoch = dt.datetime.fromtimestamp(0)
        start_date = epoch if start_date < epoch else start_date
        success = getattr(self.polygon, self.topbar['security'].value.lower())(
            symbol,
            timeframe=self.topbar['timeframe'].value,
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=self.end_date,
            limit=self.limit,
            live=self.live
        )
        self.spinner(False)
        self.crosshair(vert_visible=True, horz_visible=True) if success else None
        return success

    async def on_search(self, searched_string): self.topbar['symbol'].set(searched_string if self._binance(searched_string) else '')

    async def _on_timeframe_selection(self):
        self._binance(self.topbar['symbol'].value) if self.topbar['symbol'].value else None

    async def _on_security_selection(self):
        sec_type = self.topbar['security'].value
        self.volume_enabled = False if sec_type == 'Index' else True

        precision = 5 if sec_type == 'Forex' else 2
        min_move = 1 / (10 ** precision)  # 2 -> 0.1, 5 -> 0.00005 etc.
        self.run_script(f'''
        {self.chart.id}.series.applyOptions({{
            priceFormat: {{precision: {precision}, minMove: {min_move}}}
        }})''')
