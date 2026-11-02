from .core import rub
import pandas as pd


def cash(value):
    return f'{rub(value):,.2f} ₽'.replace(',',' ').replace('.',',')


def details_frame(details):
    frame=pd.DataFrame(details)
    if not frame.empty:
        for key in ('amount','cost','tax','profit'):frame[key]=frame[key].map(lambda v:float(rub(v)))
    return frame
