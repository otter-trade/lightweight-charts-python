import random
import time

import pandas as pd
from lightweight_charts import Chart


def calculate_sma(data: pd.DataFrame, period: int = 50):
    def avg(d: pd.DataFrame):
        return d['close'].mean()
    result = []
    for i in range(period - 1, len(data)):
        val = avg(data.iloc[i - period + 1:i])
        result.append({'time': data.iloc[i]['date'], 'value': val})
    return pd.DataFrame(result)


def straight_line(data: pd.DataFrame, period: int = 50):
    result = []
    two_points = data.sample(2)
    print(two_points)
    slope = (two_points.iat[1, 5] - two_points.iat[0, 5]) / (int(two_points.iat[1, 0]) - int(two_points.iat[0, 0]))
    for i in range(len(data)):
       val = slope * (i - int(two_points.iat[0, 0])) + two_points.iat[0, 5]
       print(val)
       result.append({'time': data.iloc[i]['date'], 'value': val})
    print(slope)

    return pd.DataFrame(result)


if __name__ == '__main__':

    chart = Chart()

    df = pd.read_csv('ohlcv.csv')
    chart.set(df)

    line = chart.create_line()
    sma_data = straight_line(df)
    line.set(sma_data)

    chart.show(block=False)

    while 1:
        line = chart.create_line()
        sma_data = straight_line(df)
        line.set(sma_data)
        time.sleep(3)


