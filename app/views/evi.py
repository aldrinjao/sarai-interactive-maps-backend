# evi.py
#
# Copyright(c) Exequiel Ceasar Navarrete <esnavarrete1@up.edu.ph>
# Licensed under MIT
# Version 1.0.0-alpha6

import ee
import csv
import StringIO
from datetime import datetime
from flask import Blueprint, jsonify, request, abort, make_response
from flask_cors import cross_origin
from app.gzipped import gzipped
from app import EE_CREDENTIALS, cache, app

mod = Blueprint('evi', __name__, url_prefix='/evi')

def get_province_geometry(province):
  ft = "ft:%s" % app.config['PROVINCES_FT']['LOCATION_METADATA_FUSION_TABLE']
  province_ft = ee.FeatureCollection(ft)
  prov_filter = ee.Filter.eq(app.config['PROVINCES_FT']['LOCATION_FUSION_TABLE_NAME_COLUMN'], province)

  return province_ft.filter(prov_filter).geometry()

def evi_mapper(image):
  hansen_image = ee.Image('UMD/hansen/global_forest_change_2013')
  data = hansen_image.select('datamask')
  mask = data.eq(1)
  return image.updateMask(mask)

def time_series_mapper(item):
  prefix = ''

  prefixed_date = str(item[0])
  date = prefixed_date.replace('_', '-')

  evi_value = float(item[4]) / 10000
  return {
    'time': date,
    'evi': evi_value
  }

def evi_clipper(image):
  ft = "ft:%s" % app.config['PROVINCES_FT']['LOCATION_METADATA_FUSION_TABLE']
  province = ee.FeatureCollection(ft)

  place = request.args.get('place')

  return image.clip(
    province.filter(ee.Filter.eq(app.config['PROVINCES_FT']['LOCATION_FUSION_TABLE_NAME_COLUMN'], place))
    .geometry()
  )

def evi_cache_key(*args, **kwargs):
  path = request.path
  args = str(hash(frozenset(request.args.items())))
  return (path + args).encode('utf-8')

def query_time_series_data(lat, lng, start_date, end_date):
  cache_key = 'evi_time_series_%s_%s_%s_%s' % (lat, lng, start_date, end_date)

  final_result = cache.get(cache_key)

  if final_result is None:
    ee.Initialize(EE_CREDENTIALS)

    # create a geometry point instance for cropping data later
    point = ee.Geometry.Point(float(lng), float(lat))


    # use the MODIS satellite data (NDVI)
    modis = ee.ImageCollection('MODIS/006/MOD13Q1').select('EVI')

    # check first if the resulting date filter yields greater than 0 features
    filtering_result = modis.filterDate(start_date, end_date)
    if len(filtering_result.getInfo()['features']) == 0:
      return None

    result = filtering_result.getRegion(point, 250).getInfo()
    result.pop(0)
    final_result = map(time_series_mapper, result)

    # cache it for 12 hours
    cache.set(cache_key, final_result, timeout=43200)
  return final_result

def query_doy_data(lat, lng, start_date, end_date):
  cache_key = 'evi_doy_%s_%s_%s_%s' % (lat, lng, start_date, end_date)

  processed = cache.get(cache_key)

  # perform query processing when its not on the cache

  if processed is None:
    query_result = query_time_series_data(lat, lng, start_date, end_date)

    processed = {}
    for value in query_result:
      parsed_date = datetime.strptime(value['time'], '%Y-%m-%d')
      doy = int(parsed_date.strftime('%j'))
      year = parsed_date.strftime('%Y')

      if processed.get(year) is None:
        processed[year] = {}

      processed[year][doy] = value['evi']

    # cache it for 12 hours
    cache.set(cache_key, processed, timeout=43200)

  return processed

# cache the result of this endpoint for 12 hours
@mod.route('/<start_date>/<end_date>', methods=['GET'])
@cross_origin()
@gzipped
@cache.cached(timeout=43200, key_prefix=evi_cache_key)

def date_and_range(start_date, end_date):
  ee.Initialize(EE_CREDENTIALS)

  geometric_bounds = ee.List([
    [127.94248139921513, 5.33459854167601],
    [126.74931782819613, 11.825234466620996],
    [124.51107186428203, 17.961503806746318],
    [121.42999903167879, 19.993626604011016],
    [118.25656974884657, 18.2117821750514],
    [116.27168958893185, 6.817365082528201],
    [122.50121143769957, 3.79887124351577],
    [127.94248139921513, 5.33459854167601]
  ])
  print "222"
  geometry = ee.Geometry.Polygon(geometric_bounds, 'EPSG:4326', True)

  # performing filterBounds will not work on this since this is a global composite
  # see similar issue with discussion:
  # <https://groups.google.com/forum/#!searchin/google-earth-engine-developers/filterbounds%7Csort:relevance/google-earth-engine-developers/__3vYdhh22k/wIE-INHXBQAJ>
  image_collection = ee.ImageCollection('MODIS/006/MOD13A1').select('EVI')
  
  # filtered = image_collection.filterDate(start_date, end_date).filterBounds(geometry).map(ndvi_mapper)
  filtered = image_collection.filterDate(start_date, end_date).map(evi_mapper)

  # WORKAROUND: perform temporal reduction using mean/median reducers to clip it to a certain geometry
  filtered = filtered.mean()
  if request.args.get('place') is not None:
    filtered = filtered.clip(get_province_geometry(request.args.get('place')))
  else:
    filtered = filtered.clip(geometry)


  
  evi = filtered
  map_parameters = {
    'min': 0,
    'max': 9000,
    'palette': 'FFFFFF, CE7E45, DF923D,F1B555, FCD163, 99B718, 74A901, 66A000, 529400, 3E8601, 207401, 056201, 004C00, 023B01, 012E01, 011D01, 011301'
  }
  # 
  map_object = evi.getMapId(map_parameters)
  map_id = map_object['mapid']
  map_token = map_object['token']

  # assemble the resulting response
  result = {
    'success': True,
    'mapId': map_id,
    'mapToken': map_token
  }

  return jsonify(**result)

@mod.route('/time-series/<lat>/<lng>/<start_date>/<end_date>', methods=['GET'])
@cross_origin()
@gzipped
def time_series(lat, lng, start_date, end_date):
  
  
  query_result = query_time_series_data(lat, lng, start_date, end_date)
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
    abort(404, 'EVI data not found')

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

    cw.writerow(['Date', 'EVI'])
    for value in query_result:
      cw.writerow([
        value['time'],
        value['evi']
      ])

    filename = 'evi-time-series-%s-%s-%s-%s' % (lat, lng, start_date, end_date)

    response = make_response(si.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=%s.csv' % filename
    response.headers['Content-type'] = 'text/csv'

  return response

@mod.route('/day-of-the-year/<lat>/<lng>/<start_date>/<end_date>', methods=['GET'])
@cross_origin()
@gzipped
def day_of_the_year(lat, lng, start_date, end_date):
  query_result = query_doy_data(lat, lng, start_date, end_date)
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
    abort(404, 'EVI data not found')

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

    csv_header_row = query_result.keys()

    # sort the items
    csv_header_row.sort()

    # save the sorted keys
    years = csv_header_row[:]

    # add the DOY to the first element of the row
    csv_header_row.insert(0, 'Day of the Year')

    # loop through all the doy and years and assemble the csv rows
    cw.writerow(csv_header_row)
    for doy in range(1, 365, 16):
      row = [doy]

      for year in years:
        row.append(query_result[year].get(doy, ''))

      cw.writerow(row)

    filename = 'evi-doy-%s-%s-%s-%s' % (lat, lng, start_date, end_date)

    response = make_response(si.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=%s.csv' % filename
    response.headers['Content-type'] = 'text/csv'

  return response


