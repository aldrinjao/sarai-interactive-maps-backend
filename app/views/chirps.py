# chirps.py
#
# Copyright(c) Exequiel Ceasar Navarrete <esnavarrete1@up.edu.ph>
# Licensed under MIT
# Version 1.0.0-alpha6

import ee
import csv
import StringIO
from datetime import datetime
from flask import Blueprint, jsonify, abort, request, make_response
from flask_cors import cross_origin
from app import EE_CREDENTIALS, cache, app
from app.gzipped import gzipped

mod = Blueprint('chirps', __name__, url_prefix='/chirps')

def accumulate(image, ee_list):
  previous = ee.Image(ee.List(ee_list).get(-1))

  added = image.add(previous).set('system:time_start', image.get('system:time_start'))

  return ee.List(ee_list).add(added)

def cumulative_mapper(item):
  timestamp = item[3] / 1000
  rainfall_0p = item[4]
  rainfall = item[5]

  # round to 2 decimal places if it has value
  if rainfall is not None:
    rainfall = round(rainfall, 2)

  return {
    'time': datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d'),
    'rainfall_0p': rainfall_0p,
    'rainfall': rainfall
  }

def rainfall_mapper(item):
  timestamp = item[3] / 1000
  rainfall = item[4]

  # round to 2 decimal places if it has value
  if rainfall is not None:
    rainfall = round(rainfall, 2)

  return {
    'time': datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d'),
    'rainfall': rainfall
  }

def rainfall_clipper(image):
  ft = "ft:%s" % app.config['PROVINCES_FT']['LOCATION_METADATA_FUSION_TABLE']
  province = ee.FeatureCollection(ft)

  place = request.args.get('place')

  return image.clip(
    province.filter(ee.Filter.eq(app.config['PROVINCES_FT']['LOCATION_FUSION_TABLE_NAME_COLUMN'], place))
    .geometry()
  )

def rainfall_cache_key(*args, **kwargs):
  path = request.path
  args = str(hash(frozenset(request.args.items())))
  return (path + args).encode('utf-8')

def query_daily_rainfall_data(lat, lng, start_date, end_date):
  cache_key = 'rainfall_daily_rain_%s_%s_%s_%s' % (lat, lng, start_date, end_date)

  final_result = cache.get(cache_key)

  if final_result is None:
    ee.Initialize(EE_CREDENTIALS)

    # create a geometry point instance for cropping data later
    point = ee.Geometry.Point(float(lng), float(lat))

    image_collection = ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY')
    filtering_result = image_collection.filterDate(start_date, end_date)

    # check if there are features retrieved
    if len(filtering_result.getInfo()['features']) == 0:
      return None

    # precipitation should be casted to float or else
    # it will throw error about incompatible types
    result = filtering_result.cast({'precipitation': 'float'}, ['precipitation']).getRegion(point, 500).getInfo()

    # remove the headers from the
    result.pop(0)

    # transform the data
    final_result = map(rainfall_mapper, result)

    # cache it for 12 hours
    cache.set(cache_key, final_result, timeout=43200)

  return final_result

def query_cumulative_rainfall_data(lat, lng, start_date, end_date):
  cache_key = 'rainfall_cum_rain_%s_%s_%s_%s' % (lat, lng, start_date, end_date)

  final_result = cache.get(cache_key)

  if final_result is None:
    ee.Initialize(EE_CREDENTIALS)

    # create a geometry point instance for cropping data later
    point = ee.Geometry.Point(float(lng), float(lat))

    image_collection = ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY')
    filtering_result = image_collection.filterDate(start_date, end_date)

    # check if there are features retrieved
    if len(filtering_result.getInfo()['features']) == 0:
      return None

    time0 = filtering_result.first().get('system:time_start')
    first = ee.List([
      ee.Image(0).set('system:time_start', time0).select([0], ['precip'])
    ])

    cumulative = ee.ImageCollection(ee.List(filtering_result.iterate(accumulate, first)))

    # precipitation should be casted to float or else
    # it will throw error about incompatible types
    result = cumulative.cast({'precipitation': 'float', 'precip': 'float'},
                             ['precip', 'precipitation']).getRegion(point, 500).getInfo()

    # remove the headers from the
    result.pop(0)

    # transform the data
    final_result = map(cumulative_mapper, result)

    # delete the first item if the rainfall_0p is not none
    if final_result[0]['rainfall_0p'] is not None:
      final_result.pop(0)

    # remove the rainfall_0p
    for item in final_result:
      item.pop('rainfall_0p', None)

    # cache it for 12 hours
    cache.set(cache_key, final_result, timeout=43200)

  return final_result

# cache the result of this endpoint for 12 hours
@mod.route('/<start_date>/<end_date>', methods=['GET'])
@cross_origin()
@gzipped
@cache.cached(timeout=43200, key_prefix=rainfall_cache_key)
def index(start_date, end_date):
  ee.Initialize(EE_CREDENTIALS)

  geometry = ee.Geometry.Polygon(
    ee.List([
      [127.94248139921513, 5.33459854167601],
      [126.74931782819613, 11.825234466620996],
      [124.51107186428203, 17.961503806746318],
      [121.42999903167879, 19.993626604011016],
      [118.25656974884657, 18.2117821750514],
      [116.27168958893185, 6.817365082528201],
      [122.50121143769957, 3.79887124351577],
      [127.94248139921513, 5.33459854167601]
    ]),
    'EPSG:4326',
    True
  )



# 
  # start_date_array = start_date.split('-')
  # end_date_array = end_date.split('-')


  image_collection = ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY')
  
  # image = image_collection.filter(ee.Filter.calendarRange(int(start_date_array[0]),int(end_date_array[0]),'year'))
  # image = image_collection.filter(ee.Filter.calendarRange(int(start_date_array[1]),int(end_date_array[1]),'year'))
  # print image

  
  image = image_collection.filterDate(start_date, end_date)

  sld_intervals = '<RasterSymbolizer>' + '<ColorMap  type="intervals" extended="false" >' + '<ColorMapEntry color="#FFFFFF" quantity="0" label="0"/>' +  '<ColorMapEntry color="#E0E0E0" quantity="50" label="50"/>' +  '<ColorMapEntry color="#CAE5FE" quantity="100" label="100" />' +  '<ColorMapEntry color="#6FBEFD" quantity="200" label="200 " />' +  '<ColorMapEntry color="#4662FD" quantity="300" label="300" />' +  '<ColorMapEntry color="#1C2371" quantity="400" label="400" />' +  '<ColorMapEntry color="#0000FF" quantity="500" label="500" />' + '<ColorMapEntry color="#000000" quantity="3000" label="3000" />' +'</ColorMap>' + '</RasterSymbolizer>'

  if request.args.get('place') is not None:
    image = image.map(rainfall_clipper)

  new_image = image.sum().clip(geometry).select('precipitation')
  new_image.sldStyle(sld_intervals)

  try:
    rainfall = new_image.sldStyle(sld_intervals)
    visualization_styles = {
      'min': 0,
      'max': 500,
      'opacity': 0.8,
      'palette': 'E0E0E0, CAE5FE, 6FBEFD, 4662FD,1C2371,000000'
    }

    map_object = rainfall.getMapId()
    map_id = map_object['mapid']
    map_token = map_object['token']

    # assemble the resulting response
    result = {
      'success': True,
      'mapId': map_id,
      'mapToken': map_token
    }
  except ee.ee_exception.EEException:
    abort(404, 'Rainfall data not found.')

  return jsonify(**result)

# cache the result of this endpoint for 12 hours
@mod.route('/daily-rainfall/<lat>/<lng>/<start_date>/<end_date>', methods=['GET'])
@cross_origin()
@gzipped
def daily_rainfall(lat, lng, start_date, end_date):
  query_result = query_daily_rainfall_data(lat, lng, start_date, end_date)
  output_format = 'json'
  available_formats = ['json', 'csv']
  requested_format = request.args.get('fmt')

  if requested_format is not None:
    # abort the request and throw HTTP 400 since the format
    # is not on the list of available formats
    if not requested_format in available_formats:
      abort(400, 'Unsupported format')

    # override the default output format
    output_format = requested_format

  # abort the request if the query_result contains None value
  if query_result is None:
    abort(404, 'Rainfall data not found')

  response = None

  if output_format == 'json':
    json_result = {
      'success': True,
      'result': query_result
    }

    response = jsonify(**json_result)
  else:
    si = StringIO.StringIO()
    cw = csv.writer(si)

    cw.writerow(['Date', 'Precipitation'])
    for value in query_result:
      cw.writerow([
        value['time'],
        value['rainfall']
      ])

    filename = 'dailt-rainfall-%s-%s-%s-%s' % (lat, lng, start_date, end_date)

    response = make_response(si.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=%s.csv' % filename
    response.headers['Content-type'] = 'text/csv'

  return response

# cache the result of this endpoint for 12 hours
@mod.route('/cumulative-rainfall/<lat>/<lng>/<start_date>/<end_date>', methods=['GET'])
@cross_origin()
@gzipped
def cumulative_rainfall(lat, lng, start_date, end_date):
  query_result = query_cumulative_rainfall_data(lat, lng, start_date, end_date)
  output_format = 'json'
  available_formats = ['json', 'csv']
  requested_format = request.args.get('fmt')

  if requested_format is not None:
    # abort the request and throw HTTP 400 since the format
    # is not on the list of available formats
    if not requested_format in available_formats:
      abort(400, 'Unsupported format')

    # override the default output format
    output_format = requested_format

  # abort the request if the query_result contains None value
  if query_result is None:
    abort(404, 'Rainfall data not found')

  response = None

  if output_format == 'json':
    json_result = {
      'success': True,
      'result': query_result
    }

    response = jsonify(**json_result)
  else:
    si = StringIO.StringIO()
    cw = csv.writer(si)

    cw.writerow(['Date', 'Precipitation'])
    for value in query_result:
      cw.writerow([
        value['time'],
        value['rainfall']
      ])

    filename = 'cumulative-rainfall-%s-%s-%s-%s' % (lat, lng, start_date, end_date)

    response = make_response(si.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=%s.csv' % filename
    response.headers['Content-type'] = 'text/csv'

  return response


