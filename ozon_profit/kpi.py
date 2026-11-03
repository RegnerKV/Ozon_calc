"""Owner-approved KPI thresholds and comparable previous-month windows."""
from calendar import monthrange
from datetime import date,timedelta
from .core import money


def previous_month(value):
    year,month=(value.year-1,12) if value.month==1 else (value.year,value.month-1)
    return date(year,month,min(value.day,monthrange(year,month)[1]))


def comparison_period(start,end):
    start=date.fromisoformat(str(start));end=date.fromisoformat(str(end))
    previous_end=previous_month(end)
    if start.day==1 and (start.year,start.month)==(end.year,end.month):
        previous_start=previous_end.replace(day=1)
        if end.day==monthrange(end.year,end.month)[1]:
            previous_end=previous_end.replace(day=monthrange(previous_end.year,previous_end.month)[1])
    else:previous_start=previous_end-(end-start)
    return previous_start,previous_end


def tone(kind,value,previous=None):
    if value is None:return 'neutral'
    value=money(value)
    if kind=='profit':return 'good' if value>0 else 'bad' if value<0 else 'neutral'
    if kind in ('revenue','net'):
        if previous is None:return 'neutral'
        return 'good' if value>=money(previous) else 'bad'
    if kind=='drr':return 'good' if value<=10 else 'bad'
    if kind=='margin':return 'good' if value>=25 else 'bad'
    return 'neutral'


def metric(target,key,label,value,state='neutral',delta=None,help=None):
    import streamlit as st
    palette={'good':('#d4e8d8','#c5ddcb'),'bad':('#f0d0d2','#e6bfc2'),'neutral':('#f2f3f5','#e5e7eb')}
    background,border=palette[state]
    with target.container(key='kpi_'+key):
        st.html('<style>.st-key-kpi_'+key+' [data-testid="stMetric"]{background:'+background+';border-color:'+border+';box-shadow:none}</style>')
        st.metric(label,value,delta=delta,help=help)
