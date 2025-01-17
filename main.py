#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Mar 25 06:43:11 2019

@author: Mirko
"""
from cv2 import split
from flask import (
    Flask, render_template, request, redirect, flash, url_for, session, g
)

from werkzeug.security import check_password_hash, generate_password_hash

from werkzeug.exceptions import abort

from psycopg2 import (
    connect
)
import requests
import json
from sqlalchemy import create_engine, null
import pandas as pd
from pandas_profiling import ProfileReport
import geopandas as gpd
from jinja2 import Environment, FileSystemLoader
from pyproj import Proj, transform
from bokeh.plotting import figure#, output_file
from bokeh.resources import CDN
from bokeh.embed import file_html
from bokeh.models import ColumnDataSource, LabelSet
from bokeh.tile_providers import CARTODBPOSITRON, get_provider

import contextily as ctx
import numpy as np
import matplotlib.pyplot as plt
from shapely.geometry import Point, Polygon
import folium

env = Environment(loader=FileSystemLoader('.'))

# Create the application instance
app = Flask(__name__, template_folder="templates")
# Set the secret key to some random bytes. Keep this really secret!
app.secret_key = b'_5#y2L"F4Q8z\n\xec]/'


def get_dbConn():
    if 'dbConn' not in g:
        myFile = open('dbConfig.txt')
        connStr = myFile.readline()
        g.dbConn = connect(connStr)
    print(g.dbConn)
    return g.dbConn


def close_dbConn():
    if 'dbConn' in g:
        g.dbComm.close()
        g.pop('dbConn')


def get_json_API(city):
    link = "https://api.waqi.info/feed/" + city + \
        "/?token=6b937a38a89b944787d29b8afca33fe1cf375bd1"
    response = requests.get(link)

    if str(response) != "<Response [200]>":
        txt = "Invalid city name. No data found for: " + city
        raise Exception(txt)

    raw_data = response.text
    data = json.loads(raw_data)
    return data

def get_forecast_data_to_DB(city):
    data = get_json_API(city)

    # from JSON to Pandas DataFrame: creating the forecast table

    # extracting all the factors seperately:
    data_df_forecast_o3 = pd.json_normalize(
        data['data']['forecast']['daily']['o3'])
    data_df_forecast_pm10 = pd.json_normalize(
        data['data']['forecast']['daily']['pm10'])
    data_df_forecast_pm25 = pd.json_normalize(
        data['data']['forecast']['daily']['pm25'])
    data_df_forecast_uvi = pd.json_normalize(
        data['data']['forecast']['daily']['uvi'])

    # preparing each of them to be merged later:
    data_df_forecast_o3 = data_df_forecast_o3.rename(
        columns={'avg': 'avg_o3', 'max': 'max_o3', 'min': 'min_o3'})
    data_df_forecast_o3.insert(0, 'day', data_df_forecast_o3.pop('day'))

    data_df_forecast_pm10 = data_df_forecast_pm10.rename(
        columns={'avg': 'avg_pm10', 'max': 'max_pm10', 'min': 'min_pm10'})
    data_df_forecast_pm10.insert(0, 'day', data_df_forecast_pm10.pop('day'))

    data_df_forecast_pm25 = data_df_forecast_pm25.rename(
        columns={'avg': 'avg_pm25', 'max': 'max_pm25', 'min': 'min_pm25'})
    data_df_forecast_pm25.insert(0, 'day', data_df_forecast_pm25.pop('day'))

    data_df_forecast_uvi = data_df_forecast_uvi.rename(
        columns={'avg': 'avg_uvi', 'max': 'max_uvi', 'min': 'min_uvi'})
    data_df_forecast_uvi.insert(0, 'day', data_df_forecast_uvi.pop('day'))

    # merging all the factors in one prediction table:
    o3_pm10 = pd.merge(data_df_forecast_o3,
                       data_df_forecast_pm10, how="outer", on=["day"])
    o3_pm10_pm25 = pd.merge(
        o3_pm10, data_df_forecast_pm25, how="outer", on=["day"])
    final_forecast_table = pd.merge(
        o3_pm10_pm25, data_df_forecast_uvi, how="outer", on=["day"])

    return final_forecast_table

def get_forecast_data(city):
    data = get_json_API(city)
    final_forecast_table = get_forecast_data_to_DB(city)
    
    #extracting lon and lat:
    data_df = pd.json_normalize(data['data'])
    
    final_forecast_table['lat'] = data_df['city.geo'][0][0]
    final_forecast_table['lon'] = data_df['city.geo'][0][1]

    final_forecast_table_html = final_forecast_table.dropna(thresh=6).to_html(index=False)
    final_forecast_table_html = final_forecast_table_html.replace("class=\"dataframe\"","id=\"forecastTable\"")
    return final_forecast_table_html


def get_realtime_data(city):
    data = get_json_API(city)
    
    #from JSON to Pandas DataFrame: creating the real time data table
    data_df_day = pd.json_normalize(data['data'])
    data_df_day["date"] = data_df_day["time.s"] + data_df_day["time.tz"]
    #dropping the unnecessary columns:
    data_df_day = data_df_day.drop(columns=['idx','attributions', 'dominentpol', 'city.url', 'city.location', 'time.v', 'time.iso',
                             'forecast.daily.o3', 'forecast.daily.pm10', 'forecast.daily.pm25', 'forecast.daily.uvi', 'debug.sync', 
                             'time.s', 'time.tz'])
    if city == 'skopje' or city == 'krakow':
        data_df_day = data_df_day.drop(columns=['iaqi.dew.v', 'iaqi.wg.v'])
    if city == 'belgrad':
        data_df_day = data_df_day.drop(columns=['iaqi.wg.v'])
    
    #renaming the columns we will be using for clarity:
    data_df_day = data_df_day.rename(columns={'aqi': 'air quality', 'city.name': 'city', 'iaqi.co.v': 'carbon monoxyde', 
                                              'iaqi.h.v':'relative humidity', 'iaqi.no2.v':'nitrogen dioxide', 
                                              'iaqi.o3.v': 'ozone', 'iaqi.p.v':'atmospheric pressure', 'iaqi.pm10.v':'PM10', 
                                              'iaqi.pm25.v':'PM2.5','iaqi.so2.v':'sulphur dioxide', 'iaqi.t.v':'temperature',
                                              'iaqi.w.v':'wind'})
    
    #creating two columns for geographical coordinates instead of one for easier access:
    data_df_day['lat'] = data_df_day['city.geo'][0][0]
    data_df_day['lon'] = data_df_day['city.geo'][0][1]
    data_df_day = data_df_day.drop(columns=['city.geo'])
    
    final_realtime_table = gpd.GeoDataFrame(data_df_day, geometry=gpd.points_from_xy(data_df_day['lon'], data_df_day['lat']))
    
    final_realtime_table_html = final_realtime_table.to_html(index=False)

    return final_realtime_table_html

def get_data_to_DataFrame(city, User):
    data = get_json_API(city)

    # from JSON to Pandas DataFrame: creating the real time data table
    data_df_day = pd.json_normalize(data['data'])
    data_df_day["date"] = data_df_day["time.s"] + data_df_day["time.tz"]

    # dropping the unnecessary columns:
    data_df_day = data_df_day.drop(columns=['idx', 'attributions', 'dominentpol', 'city.url', 'city.location', 'time.v', 'time.iso',
                                            'forecast.daily.o3', 'forecast.daily.pm10', 'forecast.daily.pm25', 'forecast.daily.uvi', 'debug.sync'])

    # renaming the columns we will be using for clarity:
    data_df_day = data_df_day.rename(columns={'city.name': 'city',
                                              'aqi': 'air_quality',
                                              'iaqi.co.v': 'carbon_monoxyde',
                                              'iaqi.h.v': 'relative_humidity',
                                              'iaqi.no2.v': 'nitrogen_dioxide',
                                              'iaqi.o3.v': 'ozone', 
                                              'iaqi.p.v': 'atmospheric_pressure', 
                                              'iaqi.pm10.v': 'PM10',
                                              'iaqi.pm25.v': 'PM25', 
                                              'iaqi.so2.v': 'sulphur_dioxide',
                                              'iaqi.t.v': 'temperature',
                                              'iaqi.w.v': 'wind', 
                                              'time.s': 'date_and_time', 
                                              'time.tz': 'time zone'
                                              })

    # creating two columns for geographical coordinates instead of one for easier access:
    data_df_day['x'], data_df_day['y'] = transform(Proj(init='epsg:4326'), Proj(init='epsg:3857'), data_df_day['city.geo'][0][1], data_df_day['city.geo'][0][0])
    data_df_day = data_df_day.drop(columns=['city.geo'])
    data_df_day = data_df_day.drop(columns=['time zone'])
    final_realtime_table = gpd.GeoDataFrame(
        data_df_day, geometry=gpd.points_from_xy(data_df_day['x'], data_df_day['y']))
    
    final_realtime_table['ID']=User #if you don't use User, here you have to comment it
    return final_realtime_table

def connStr():
    myFile = open('dbConfig.txt')
    [dbname,user,password] = [x.split(sep="=")[1] for x in myFile.readline().split()]
    return "postgresql://" + user + ":" + password + "@localhost:5432/" + dbname

def sendDFtoDB(db):
    engine = create_engine(connStr()) 
    db.to_postgis('cities', engine, if_exists = 'replace', index=False) #I can put some queries here
    
def update_data_on_DB(db):
    engine = create_engine(connStr()) 
    Data = gpd.GeoDataFrame.from_postgis('cities', engine, geom_col='geometry')
    DataNew = Data.append(db)
    return DataNew


# Function to retrieve station coordinates and names in the more info section
def translate_data(response):
    raw_data = response.text
    data = json.loads(raw_data)
    df_stations = pd.json_normalize(data['data'])
    gdf = gpd.GeoDataFrame(df_stations["station.name"],
                           geometry=gpd.points_from_xy(df_stations.lat, df_stations.lon))
    G = gdf.set_crs('epsg:4326')
    G.rename(columns={'station.name': 'Station_Name'}, inplace=True, errors='raise')
    coordinate_list = [(x,y) for x,y in zip(G.geometry.x , G.geometry.y)]
    return coordinate_list, G.Station_Name, df_stations.aqi
    
def project_html(data, html_type = null):
    if html_type == "table1":					
        return "                    <div class=\"box\">\
						<header>\
							<h2>Realtime data</h2>\
						</header>\
						<div class=\"table-wrapper\">" + data + "</div>\
					</div>"

    if html_type == "table2":
        return "<div class=\"box\">\
						<h2>Forecast data</h2>\
						<div class=\"table-wrapper\">" + data + "</div>\
						<p><em>To sort data click column header</em></p>\
						<form method=\"post\" action=\"#\">\
							<div class=\"col-12\">\
                                <p><em>To filter, first select column, then filtering type and lastly, type filter value in \"Filter forecast...\" input.</em></p>\
                                <div class=\"row gtr-uniform\">\
								<div class=\"col-6 col-12-xsmall\">\
                                <select name=\"fcolumn\" id=\"fcolumn\" onchange=\"check()\" required>\
									<option value=\"\" disabled selected>Column name</option>\
									<option value=\"day\">Day</option>\
									<option value=\"avg_o3\">Average O3</option>\
									<option value=\"max_o3\">Maximum O3</option>\
									<option value=\"min_o3\">Minimum O3</option>\
									<option value=\"avg_pm10\">Average pm10</option>\
									<option value=\"max_pm10\">Maximum pm10</option>\
									<option value=\"min_pm10\">Minimum pm10</option>\
									<option value=\"avg_pm25\">Average pm25</option>\
									<option value=\"max_pm25\">Maximum pm25</option>\
									<option value=\"min_pm25\">Minimum pm25</option>\
									<option value=\"avg_uvi\">Average UVI</option>\
									<option value=\"max_uvi\">Maximum UVI</option>\
									<option value=\"min_uvi\">Minimum UVI</option>\
								</select>\
                                </div>\
                                <div class=\"col-6 col-12-xsmall\">\
                                <p><b>Column</b></p>\
                                </div>\
                                </div>\
                                <div class=\"row gtr-uniform\">\
								<div class=\"col-6 col-12-xsmall\">\
								<select name=\"ftype\" id=\"ftype\" onchange=\"check()\" required>\
									<option value=\"\" disabled selected>Filter type</option>\
									<option value=\"equal\">==</option>\
									<option value=\"gt\">&gt;</option>\
									<option value=\"lt\">&lt;</option>\
									<option value=\"egt\">&gt;=</option>\
									<option value=\"elt\">&lt;=</option>\
								</select>\
                                </div>\
                                <div class=\"col-6 col-12-xsmall\">\
                                <p><b>Type</b></p>\
                                </div>\
                                </div>\
                                <div class=\"row gtr-uniform\">\
								<div class=\"col-6 col-12-xsmall\">\
										<input type=\"text\" id=\"filterInput\" placeholder=\"Filter forecast...\" disabled>\
                                </div>\
                                <div class=\"col-6 col-12-xsmall\">\
                                <p><b>Value</b></p>\
                                </div>\
                                </div>\
							</div>\
						</form>\
					 </div>"
    if html_type == "tableStat":
        return "			<div class=\"box\">\
						<h2>Analysis</h2>\
						<div class=\"table-wrapper\">" + data + "</div>\
					</div>"
    if html_type == "map":
    	return "				<div class=\"box\">\
						<h2>Map</h2>\
						<div>" + data + "</div>\
					</div>"
    
@app.route('/register', methods=('GET', 'POST'))
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        error = None

        if not username:
            error = 'Username is required.'
        elif not password:
            error = 'Password is required.'
        else:
            conn = get_dbConn()
            cur = conn.cursor()
            cur.execute(
                'SELECT user_id FROM blog_user WHERE user_name = %s', (username,))
            if cur.fetchone() is not None:
                error = 'User {} is already registered.'.format(username)
                cur.close()

        if error is None:
            conn = get_dbConn()
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO blog_user (user_name, user_password) VALUES (%s, %s)',
                (username, generate_password_hash(password))
            )
            cur.close()
            conn.commit()
            return redirect(url_for('login'))
        else:
            error = "Please register"

        flash(error)

    return render_template('auth/register.html')


@app.route('/login', methods=('GET', 'POST'))
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_dbConn()
        cur = conn.cursor()
        error = None
        cur.execute(
            'SELECT * FROM blog_user WHERE user_name = %s', (username,)
        )
        user = cur.fetchone()
        cur.close()
        conn.commit()

        if user is None:
            error = 'Incorrect username.'
        elif not check_password_hash(user[2], password):
            error = 'Incorrect password.'

        if error is None:
            session.clear()
            session['user_id'] = user[0]
            return redirect(url_for('index'))

        flash(error)

    return render_template('auth/login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


def load_logged_in_user():
    user_id = session.get('user_id')

    if user_id is None:
        g.user = None
    else:
        conn = get_dbConn()
        cur = conn.cursor()
        cur.execute(
            'SELECT * FROM blog_user WHERE user_id = %s', (user_id,)
        )
        g.user = cur.fetchone()
        cur.close()
        conn.commit()
    if g.user is None:
        return False
    else:
        return True


# Create a URL route in our application for "/"
@app.route('/')
@app.route('/index')
def index():
    load_logged_in_user()

    return render_template('index.html')


@app.route('/generic')
def generic():
    load_logged_in_user()
    return render_template('generic.html')


@app.route('/elements', methods=['GET', 'POST'])
def elements():
    template = env.get_template("templates/elements.html")
    stations = gpd.GeoDataFrame()
    stations['geometry'] = None
    response_paris = requests.get(
    'https://api.waqi.info/v2/map/bounds?latlng=48.906116,2.225504,48.813514,2.466307&networks=all&token=7b5dd86fc12812d40a2d725d9296813872fd7caa')
    response_skopje = requests.get('https://api.waqi.info/v2/map/bounds?latlng=42.057215,21.343864,41.946194,21.523439&networks=all&token=7b5dd86fc12812d40a2d725d9296813872fd7caa')
    response_Belgrad = requests.get('https://api.waqi.info/v2/map/bounds?latlng=44.762,20.358,44.853,20.621&networks=all&token=7b5dd86fc12812d40a2d725d9296813872fd7caa')
    response_Krakow = requests.get('https://api.waqi.info/v2/map/bounds?latlng=50.018,19.79,50.185,20.189&networks=all&token=7b5dd86fc12812d40a2d725d9296813872fd7caa')
    response_London = requests.get('https://api.waqi.info/v2/map/bounds?latlng=51.722,-0.482,51.498,0.303&networks=all&token=7b5dd86fc12812d40a2d725d9296813872fd7caa')
    Paris, PG, PA = translate_data(response_paris)
    Skopje, SG, SA = translate_data(response_skopje)
    Belgrad, BG, BA = translate_data(response_Belgrad)
    Krakow, KG, KA = translate_data(response_Krakow)
    London, LG, LA = translate_data(response_London)
    map = folium.Map(location = [45.5170365,13.3888599], tiles='OpenStreetMap' , zoom_start = 5) 
    cities = [Paris,Skopje,Belgrad,Krakow,London]
    station = [PG, SG, BG, KG, LG]
    AQI = [PA, SA, BA, KA, LA]
    color = ["blue","orange","red","green","purple"]
    k=0
    for city in cities:
        for i  in range(len(city)):
            #assign a color marker for the type of volcano, Strato being the most common
            type_color = color[k]
            # Place the markers with the popup labels and data
            map= map.add_child(folium.Marker(location= city[i],popup=
                                             str(station[k][i]) + '<br>'
                                             + str(city[i]) + '<br>'
                                             '<strong>AQI level</strong> ' + str(AQI[k][i]),
                                             tooltip='<strong>Click here to see coordinates</strong>',
                                             icon=folium.Icon(color="%s" % type_color,icon="crosshairs", prefix ='fa')).add_to(map))
                                    
        k = k+1
    map.save('templates/Map/Map.html')
    load_logged_in_user()
    return render_template('elements.html')

@app.route('/Map')
def Map():
    return render_template('Map/Map.html')

@app.route('/createProject', methods=['GET', 'POST'])
def createProject():
    if load_logged_in_user():        
        user_id = session.get('user_id')
        if request.method == 'POST':
            template = env.get_template("templates/createProject.html")
    
            if request.form['dtype'] == 'F':
                if request.form['city']=='paris':
                    CityForecast = get_forecast_data_to_DB('Paris')
                elif request.form['city']=='skopje':
                    CityForecast = get_forecast_data_to_DB('Belgrad')
                elif request.form['city']=='london':#Kraków
                    CityForecast = get_forecast_data_to_DB('Skopje')
                elif request.form['city']=='belgrad':
                    CityForecast = get_forecast_data_to_DB('London')
                else:
                    CityForecast = get_forecast_data_to_DB('Krakow')
                CityForecast.dropna()
                CityForecast_html = CityForecast.to_html(index=True).replace("class=\"dataframe\"","id=\"forecastTable\"")
                Description = CityForecast.describe()
                print('\n'+request.form['city']+'\n')
                Description_html = Description.to_html(index=True)+'<a href=\"/Analysis\" target="_blank"><button class=\"btn35\">EXPORT ANALYSIS</button></a>'
                profile = ProfileReport(CityForecast, title="Forecast statistics", explorative=True)
                profile.to_file("templates/Analysis/Analysis.html")
                template_vars = {"table1": "",
                                 "table2": project_html(CityForecast_html,"table2"),
                                 "tableStat": project_html(Description_html,"tableStat"),
                                 "map": project_html(visualize_data(request.form['city'],user_id),"map")}
                html_out = template.render(template_vars)
    
            elif request.form['dtype'] == 'RT':
                C = get_data_to_DataFrame(request.form['city'],user_id)   
                DataDB = update_data_on_DB(C)
                sendDFtoDB(DataDB)
                if ('iaqi.dew.v' in DataDB.columns):
                    GDF = DataDB.drop(columns=['iaqi.dew.v','iaqi.wg.v','date_and_time','date','lat','lon','ID'])
                else:
                    GDF = DataDB.drop(columns=['date_and_time','date','lat','lon','ID'])
                if request.form['city']=='paris':
                    City = GDF.loc[DataDB['city']=='Paris']
                elif request.form['city']=='skopje':
                    City = GDF.loc[DataDB['city']=='Centar, Skopje, Macedonia (Центар)']
                    City.drop(columns=['nitrogen_dioxide'], axis = 1, inplace = True) # Because they are all NULL
                elif request.form['city']=='london':#Kraków
                    City = GDF.loc[DataDB['city']=='London']
                elif request.form['city']=='belgrad':
                    City = GDF.loc[DataDB['city']=='Beograd Vračar, Serbia']
                    City.drop(columns=['carbon_monoxyde'], axis = 1, inplace = True)
                else:
                    City = GDF.loc[DataDB['city']=='Kraków-ul. Dietla, Małopolska, Poland']
                
                City.drop(columns=['geometry', 'x','y'], axis = 1, inplace = True)
                Description = City.describe()
                print('\n'+request.form['city']+'\n')
                Description_html = Description.to_html(index=True)+'<a href=\"/Analysis\" target="_blank"><button class=\"btn35\">EXPORT ANALYSIS</button></a>'
                template_vars = {"table1": project_html(get_realtime_data(request.form['city']),"table1"),
                                 "table2": "",
                                 "tableStat": project_html(Description_html,"tableStat"),
                                "map": project_html(visualize_data(request.form['city'],user_id),"map")
                                 }
                profile = ProfileReport(City, title="Statistical tool", explorative=True)
                profile.to_file("templates/Analysis/Analysis.html")
                html_out = template.render(template_vars)
    
            elif request.form['dtype'] == 'B':
                C = get_data_to_DataFrame(request.form['city'],user_id)   
                D = update_data_on_DB(C)
                sendDFtoDB(D)
                if request.form['city']=='paris':
                    CityForecast = get_forecast_data_to_DB('Paris')
                elif request.form['city']=='skopje':
                    CityForecast = get_forecast_data_to_DB('Belgrad')
                elif request.form['city']=='london':#Kraków
                    CityForecast = get_forecast_data_to_DB('Skopje')
                elif request.form['city']=='belgrad':
                    CityForecast = get_forecast_data_to_DB('London')
                else:
                    CityForecast = get_forecast_data_to_DB('Krakow')
                CityForecast.dropna()
                CityForecast_html = CityForecast.to_html(index=True).replace("class=\"dataframe\"","id=\"forecastTable\"")
                Description = CityForecast.describe()
                Description_html = Description.to_html(index=True) +'<a href=\"/Analysis\" target="_blank"><button class=\"btn35\">EXPORT ANALYSIS</button></a>'
                profile = ProfileReport(CityForecast, title="Forecast statistics", explorative=True)
                profile.to_file("templates/Analysis/Analysis.html")
                template_vars = {"table1": project_html(get_realtime_data(request.form['city']),"table1"),
                                 "table2": project_html(CityForecast_html,"table2"),
                                 "tableStat": project_html(Description_html,"tableStat"),
                                 "map": project_html(visualize_data(request.form['city'],user_id),"map")}
                html_out = template.render(template_vars)
    
            else:
                template_vars = {"table1": '\nInvalid data type! Inputs can be: "F", "RT" or "B"!',
                                 "table2": "",
                                 "tableStat":"",
                                 "map": ""}
                html_out = template.render(template_vars)
    
            return html_out
            # return render_template('createProject.html', tables=get_data(request.form['query']))
    
        return render_template('createProject.html')
    else :
        error = 'Only loggedin users can insert posts!'
        flash(error)
        return redirect(url_for('login'))
    
    
@app.route('/Analysis')
def Analysis():
    return render_template('Analysis/Analysis.html')



def visualize_data(city, User):
        df = get_data_to_DataFrame(city, User).drop(columns = ["geometry"])
        psource = ColumnDataSource(df)
        TOOLTIPS = [    ("name", "@city"),
                        ("air quality", "@air_quality"),
                        ("temperature","@temperature"),
                        ("wind", "@wind"),
                        ("atmospheric pressure", "@atmospheric_pressure"),
                        ("PM10", "@PM10"),
                        ("PM25", "@PM25"),
                        ("local date","@date_and_time")]
        p1 = figure(x_range=(int(df['x']) - 4000, int(df['x']) + 4000), y_range=(int(df['y']) - 4000, int(df['y']) + 4000),
           x_axis_type="mercator", y_axis_type="mercator", tooltips=TOOLTIPS)
        p1.add_tile(get_provider(CARTODBPOSITRON)) 
        p1.circle('x', 'y', source=psource, color='red', radius=50) #ICON(map-marker)
        labels = LabelSet(x='x', y='y', text='ID', level="glyph",
              x_offset=5, y_offset=5, source=psource, render_mode='css')
        p1.add_layout(labels)
        html = file_html(p1, CDN, "my plot")
        
        return html
        

# If we're running in stand alone mode, run the application
if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)

