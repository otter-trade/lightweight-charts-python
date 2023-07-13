from lightweight_charts import Chart

if __name__ == '__main__':
    chart = Chart(volume_enabled=False)
    chart.binance.spot(
        crypto_pair='BTCUSDT',
        timeframe='5m',
        start_date='2023-06-09',
        live=True
    )
    chart.show(block=True)
