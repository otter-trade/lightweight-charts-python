from lightweight_charts import BinanceChart

if __name__ == '__main__':
    chart = BinanceChart(num_bars=200,
                         limit=5000,
                         live=True)
    chart.show(block=True)