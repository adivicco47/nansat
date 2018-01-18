# Name:    domain.py
# Purpose: Container of Domain class
# Authors:      Asuka Yamakawa, Anton Korosov, Knut-Frode Dagestad,
#               Morten W. Hansen, Alexander Myasoyedov,
#               Dmitry Petrenko, Evgeny Morozov, Aleksander Vines
# Created:      29.06.2011
# Copyright:    (c) NERSC 2011 - 2015
# Licence:
# This file is part of NANSAT.
# NANSAT is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.
# http://www.gnu.org/licenses/gpl-3.0.html
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
from __future__ import absolute_import
import re, warnings
from math import sin, pi, cos, acos, copysign
import string
from xml.etree.ElementTree import ElementTree

import numpy as np
try:
    import matplotlib.pyplot as plt
    from mpl_toolkits.basemap import Basemap
    from matplotlib.patches import Polygon
except ImportError:
    BASEMAP_LIB_EXISTS = False
else:
    BASEMAP_LIB_EXISTS = True

from nansat.tools import add_logger, initial_bearing, haversine, gdal, osr, ogr
from nansat.tools import OptionError, ProjectionError
from nansat.nsr import NSR
from nansat.vrt import VRT
    

class Domain(object):
    """Container for geographical reference of a raster

    A Domain object describes all attributes of geographical
    reference of a raster:
      * width and height (number of pixels)
      * pixel size (e.g. in decimal degrees or in meters)
      * relation between pixel/line coordinates and geographical
        coordinates (e.g. a linear relation)
      * type of data projection (e.g. geographical or stereographic)

    The core of Domain is a GDAL Dataset. It has no bands, but only
    georeference information: rasterXsize, rasterYsize, GeoTransform and
    Projection or GCPs, etc. which fully describe dimentions and spatial
    reference of the grid.

    There are three ways to store geo-reference in a GDAL dataset:
      * Using GeoTransfrom to define linear relationship between raster
        pixel/line and geographical X/Y coordinates
      * Using GCPs (set of Ground Control Points) to define non-linear
        relationship between pixel/line and X/Y
      * Using Geolocation Array - full grids of X/Y coordinates for
        each pixel of a raster
    The relation between X/Y coordinates of the raster and latitude/longitude
    coordinates is defined by projection type and projection parameters.
    These pieces of information are therefore stored in Domain:
      * Type and parameters of projection +
        * GeoTransform, or
        * GCPs, or
        * GeolocationArrays

    Domain has methods for basic operations with georeference information:
      * creating georeference from input options;
      * fetching corner, border or full grids of X/Y coordinates;
      * making map of the georeferenced grid in a PNG or KML file;
      * and some more...

    The main attribute of Domain is a VRT object self.vrt.
    Nansat inherits from Domain and adds bands to self.vrt

    """

    KML_BASE = '''<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2"
    xmlns:gx="http://www.google.com/kml/ext/2.2"
    xmlns:kml="http://www.opengis.net/kml/2.2"
    xmlns:atom="http://www.w3.org/2005/Atom">
    {content}
    </kml>'''

    # TODO: logLevel pep8
    def __init__(self, srs=None, ext=None, ds=None, lon=None,
                 lat=None, name='', logLevel=None):
        """Create Domain from GDALDataset or string options or lat/lon grids

        d = Domain(srs, ext)
            Size, extent and spatial reference is given by strings
        d = Domain(ds=GDALDataset):
            Size, extent and spatial reference is copied from input
            GDAL dataset
        d = Domain(srs, ds=GDALDataset):
            Spatial reference is given by srs, but size and extent is
            determined
            from input GDAL dataset
        d = Domain(lon=lonGrid, lat=latGrid)
            Size, extent and spatial reference is given by two grids

        Parameters
        ----------
        srs : PROJ4 or EPSG or WKT or NSR or osr.SpatialReference()
            Input parameter for nansat.NSR()
        ext : string
            some gdalwarp options + additional options
            [http://www.gdal.org/gdalwarp.html]
            Specifies extent, resolution / size
            Available options: (('-te' or '-lle') and ('-tr' or '-ts'))
            (e.g. '-lle -10 30 55 60 -ts 1000 1000' or
            '-te 100 2000 300 10000 -tr 300 200')
            -tr resolutionx resolutiony
            -ts sizex sizey
            -te xmin ymin xmax ymax
            -lle lonmin latmin lonmax latmax
        ds : GDAL dataset
        lat : Numpy array
            Grid with latitudes
        lon : Numpy array
            Grid with longitudes
        name : string, optional
            Name to be added to the Domain object
        logLevel : int, optional
            level of logging

        Raises
        -------
        ProjectionError : occurs when Projection() is empty
            despite it is required for creating extentDic.
        OptionError : occures when the arguments are not proper.

        Modifies
        ---------
        self.vrt.datasetset : dataset in memory
            dataset is created based on the input arguments

        See Also
        ---------
        Nansat.reproject()
        [http://www.gdal.org/gdalwarp.html]
        [http://trac.osgeo.org/proj/]
        [http://spatialreference.org/]
        [http://www.gdal.org/ogr/osr_tutorial.html]

        """
        # set default attributes
        self.logger = add_logger('Nansat', logLevel)
        self.name = name

        self.logger.debug('ds: %s' % str(ds))
        self.logger.debug('srs: %s' % srs)
        self.logger.debug('ext: %s' % ext)

        # If too much information is given raise error
        if ds is not None and srs is not None and ext is not None:
            raise OptionError('Ambiguous specification of both dataset, srs- and ext-strings.')

        # choose between input opitons:
        # ds
        # ds and srs
        # srs and ext
        # lon and lat

        # if only a dataset is given:
        #     copy geo-reference from the dataset
        if ds is not None and srs is None:
            self.vrt = VRT.from_gdal_dataset(ds)

        # If dataset and srs are given (but not ext):
        #   use AutoCreateWarpedVRT to determine bounds and resolution
        elif ds is not None and srs is not None:
            srs = NSR(srs)
            tmp_vrt = gdal.AutoCreateWarpedVRT(ds, None, srs.wkt)
            if tmp_vrt is None:
                raise ProjectionError('Could not warp the given dataset to the given SRS.')
            else:
                self.vrt = VRT.from_gdal_dataset(tmp_vrt)

        # If SpatialRef and extent string are given (but not dataset)
        elif srs is not None and ext is not None:
            srs = NSR(srs)
            # create full dictionary of parameters
            extent_dict = Domain._create_extent_dict(ext)

            # convert -lle to -te
            if 'lle' in extent_dict.keys():
                extent_dict = self._convert_extentDic(srs, extent_dict)

            # get size/extent from the created extent dictionary
            geo_transform, raster_x_size, raster_y_size = self._get_geotransform(extent_dict)
            # create VRT object with given geo-reference parameters
            self.vrt = VRT.from_dataset_params(x_size=raster_x_size, y_size=raster_y_size,
                                               geo_transform=geo_transform,
                                               projection=srs.wkt,
                                               gcps=[], gcp_projection='')
            self.extent_dict = extent_dict
        elif lat is not None and lon is not None:
            # create self.vrt from given lat/lon
            self.vrt = VRT.from_lonlat(lon, lat)
        else:
            raise OptionError('"dataset" or "srsString and extentString" '
                              'or "dataset and srsString" are required')

        self.logger.debug('vrt.dataset: %s' % str(self.vrt.dataset))

    def __repr__(self):
        """Creates string with basic info about the Domain object

        Modifies
        ---------
        Print size, projection and corner coordinates

        """
        corners_temp = '\t (%6.2f, %6.2f)  (%6.2f, %6.2f)\n'
        separator = '-' * 40 + '\n'

        out_str = 'Domain:[%d x %d]\n' % self.shape()[::-1]
        out_str += separator
        corners = self.get_corners()
        out_str += 'Projection:\n'
        out_str += (NSR(self.vrt.get_projection()).ExportToPrettyWkt(1) + '\n')
        out_str += separator
        out_str += 'Corners (lon, lat):\n'
        out_str += corners_temp % (corners[0][0], corners[1][0], corners[0][2], corners[1][2])
        out_str += corners_temp % (corners[0][1], corners[1][1], corners[0][3], corners[1][3])
        return out_str

    # TODO: Test write_kml
    def write_kml(self, xmlFileName=None, kmlFileName=None):
        """Write KML file with domains

        Convert XML-file with domains into KML-file for GoogleEarth
        or write KML-file with the current Domain

        Parameters
        -----------
        xmlFileName : string, optional
            Name of the XML-file to convert. If only this value is given
            - kmlFileName=xmlFileName+'.kml'

        kmlFileName : string, optional
            Name of the KML-file to generate from the current Domain

        """
        xml_filename = xmlFileName
        kml_filename = kmlFileName

        template = '''<Document>
        \t<name>{filename}</name>
        \t\t<Folder><name>{filename}</name><open>1</open>
        {borders}
        \t\t</Folder></Document>'''

        # test input options
        if xml_filename and not kml_filename:
            # if only input XML-file is given - convert it to KML

            # open XML, get all domains
            with open(xml_filename, 'rb') as xml_file:
                xml_domains = list(ElementTree(file=xml_file).getroot())

            # convert domains in XML into list of domains
            domains = [Domain(srs=xml_filename, ext=domain.attrib['name'])
                       for domain in xml_domains]

        elif not xml_filename and kml_filename:
            # if only output KML-file is given
            # then convert the current domain to KML
            domains = [self]

        else:
            # otherwise it is potentially error
            raise OptionError('Either xmlFileName(%s)\
             or kmlFileName(%s) are wrong' % (xml_filename, kml_filename))

        # get border of each domain and join them to a one string
        borders = ''.join([domain._get_border_kml() for domain in domains])
        # open KML, write the modified template
        with open(kml_filename, 'wt') as kml_file:
            kml_content = template.format(name=self.name, filename=kml_filename, borders=borders)
            kml_file.write(self.KML_BASE.format(content=kml_content))

    # TODO: Test _get_border_kml
    def _get_border_kml(self):
        """Generate Placemark entry for KML

        Returns
        --------
        kmlEntry : String
            String with the Placemark entry

        """
        klm_entry = '''\t\t\t<Placemark>
        \t\t\t\t<name>{name}</name>
        \t\t\t\t<Style>
        \t\t\t\t\t<LineStyle><color>ffffffff</color></LineStyle>
        \t\t\t\t\t<PolyStyle><fill>0</fill>'
        \t\t\t\t</Style>
        \t\t\t\t<Polygon><tessellate>1</tessellate><outerBoundaryIs><LinearRing><coordinates>
        {coordinates}
        </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>'''
        domain_lon, domain_lat = self.get_border()
        # convert Border coordinates into KML-like string
        coordinates = ''.join(['%f,%f,0 ' % (lon, lat) for lon, lat in zip(domain_lon, domain_lat)])
        return klm_entry.format(name=self.name, coordinates=coordinates)

    def write_kml_image(self, kmlFileName, kmlFigureName=None):
        """Create KML file for already projected image

        Write Domain Image into KML-file for GoogleEarth

        Parameters
        -----------
        kmlFileName : string, optional
            Name of the KML-file to generate from the current Domain
        kmlFigureName : string, optional
            Name of the projected image stored in .png format

        Examples
        ---------
        # First of all, reproject an image into Lat/Lon WGS84
          (Simple Cylindrical) projection
        # 1. Cancel previous reprojection
        # 2. Get corners of the image and the pixel resolution
        # 3. Create Domain with stereographic projection,
        #    corner coordinates and resolution 1000m
        # 4. Reproject
        # 5. Write image
        # 6. Write KML for the image
        n.reproject() # 1.
        lons, lats = n.get_corners() # 2.
        srsString = '+proj=latlong +datum=WGS84 +ellps=WGS84 +no_defs'
        extentString = '-lle %f %f %f %f -ts 3000 3000'
        % (min(lons), min(lats), max(lons), max(lats))
        d = Domain(srs=srsString, ext=extentString) # 3.
        n.reproject(d) # 4.
        n.write_figure(fileName=figureName, bands=[3], clim=[0,0.15],
                       cmapName='gray', transparency=0) # 5.
        n.write_kml_image(kmlFileName=oPath + fileName + '.kml',
                          kmlFigureName=figureName) # 6.

        """

        kml_filename = kmlFileName
        kml_figurename = kmlFigureName

        template = '''<GroundOverlay>
        \t<name>{filename}</name>
        \t<Icon>
        \t\t<href>{figurename}</href>
        \t\t<viewBoundScale>0.75</viewBoundScale>
        \t</Icon>
        \t<LatLonBox>
        \t\t<north>{north}</north>
        \t\t<south>{south}</south>
        \t\t<east>{east}</east>
        \t\t<west>{west}</west>
        \t</LatLonBox>
        </GroundOverlay>'''
        # test input options
        # TODO: kml_figurename can be not optional
        if kml_figurename is None:
            raise OptionError('kmlFigureName(%s) is not specified' % kmlFigureName)

        # get corner of the domain and add to KML
        # TODO: can we change that to max_min_lat_lon?
        domain_lon, domain_lat = self.get_corners()
        with open(kml_filename, 'wt') as kml_file:
            kml_content = template.format(filename=kml_filename, figurename=kml_figurename,
                                          north=max(domain_lat), south=min(domain_lat),
                                          east=max(domain_lon), west=min(domain_lon))
            kml_file.write(self.KML_BASE.format(content=kml_content))

    def get_geolocation_grids(self, stepSize=1, dstSRS=NSR()):
        """Get longitude and latitude grids representing the full data grid

        If GEOLOCATION is not present in the self.vrt.dataset then grids
        are generated by converting pixel/line of each pixel into lat/lon
        If GEOLOCATION is present in the self.vrt.dataset then grids are read
        from the geolocation bands.

        Parameters
        -----------
        stepSize : int
            Reduction factor if output is desired on a reduced grid size

        Returns
        --------
        longitude : numpy array
            grid with longitudes
        latitude : numpy array
            grid with latitudes
        """
        step_size = stepSize
        dst_srs = dstSRS
        x_vec = range(0, self.vrt.dataset.RasterXSize, step_size)
        y_vec = range(0, self.vrt.dataset.RasterYSize, step_size)
        x_grid, y_grid = np.meshgrid(x_vec, y_vec)

        if hasattr(self.vrt, 'geolocation') and len(self.vrt.geolocation.data) > 0:
            # if the vrt dataset has geolocationArray
            # read lon,lat grids from geolocationArray
            lon_grid, lat_grid = self.vrt.geolocation.get_geolocation_grids()
            lon_arr, lat_arr = lon_grid[y_grid, x_grid], lat_grid[y_grid, x_grid]
        else:
            # generate lon,lat grids using GDAL Transformer
            lon_vec, lat_vec = self.transform_points(x_grid.flatten(), y_grid.flatten(), dstSRS=dst_srs)
            lon_arr = lon_vec.reshape(x_grid.shape)
            lat_arr = lat_vec.reshape(x_grid.shape)

        return lon_arr, lat_arr

    def _convert_extentDic(self, dstSRS, extentDic):
        """Convert -lle option (lat/lon) to -te (proper coordinate system)

        Source SRS from LAT/LON projection and target SRS from dstWKT.
        Create osr.CoordinateTransformation based on these SRSs and
        convert given values in degrees to the destination coordinate
        system given by WKT.
        Add key 'te' and the converted values into the extentDic.

        Parameters
        -----------
        dstSRS : NSR
            Destination Spatial Reference
        extentDic : dictionary
            dictionary with 'lle' key

        Returns
        --------
        extentDic : dictionary
            input dictionary + 'te' key and its values

        """
        coorTrans = osr.CoordinateTransformation(NSR(), dstSRS)

        # convert lat/lon given by 'lle' to the target coordinate system and
        # add key 'te' and the converted values to extentDic
        # TODO: Make DRY
        x1, y1, _ = coorTrans.TransformPoint(extentDic['lle'][0], extentDic['lle'][3])
        x2, y2, _ = coorTrans.TransformPoint(extentDic['lle'][2], extentDic['lle'][3])
        x3, y3, _ = coorTrans.TransformPoint(extentDic['lle'][2], extentDic['lle'][1])
        x4, y4, _ = coorTrans.TransformPoint(extentDic['lle'][0], extentDic['lle'][1])

        minX = min([x1, x2, x3, x4])
        maxX = max([x1, x2, x3, x4])
        minY = min([y1, y2, y3, y4])
        maxY = max([y1, y2, y3, y4])

        extentDic['te'] = [minX, minY, maxX, maxY]

        return extentDic

    # TODO: Document and comment _check_parser_input
    @staticmethod
    def _check_extent_input(option_vars, params, size):
        if option_vars[0] in params:
            try:
                # Check type of input values during counting of length
                if len([float(el) for el in option_vars[1:]]) != size:
                    raise OptionError('%s requires exactly %s parameters (%s given)'
                                      % (option_vars[0], size, len(option_vars[1:])))
            except ValueError:
                raise OptionError('Input values must be int or float')
        else:
            raise OptionError('Expeced parameter is te, lle, ts, tr. (%s given)' % option_vars[0])

    @staticmethod
    def _create_extent_dict(extent_str):
        """Create a dictionary from extentString

        Check if extentString is proper.
            * '-te' and '-lle' take 4 numbers.
            * '-ts' and '-tr' take 2 numbers.
            * the combination should be ('-te' or '-lle') and ('-ts' or '-tr')
        If it is proper, create a dictionary
        Otherwise, raise the error.

        Parameters
        -----------
        extentString : string
            '-te xMin yMin xMax yMax',
            '-tr xResolution yResolution',
            '-ts width height',
            '-lle minlon minlat maxlon maxlat'

        Returns
        --------
        extentDic : dictionary
            has key ('te' or 'lle') and ('tr' or 'ts') and their values.

        Raises
        -------
        OptionError : occurs when the extent_str is improper

        """
        options = extent_str.strip().split('-')[1:]

        if len(options) != 2:
            raise OptionError('_create_extentDic requires exactly '
                              '2 parameters (%s given)' % len(options))

        options = list(map(lambda opt: opt.split(), options))
        Domain._check_extent_input(options[0], ['te', 'lle'], 4)
        Domain._check_extent_input(options[1], ['ts', 'tr'], 2)

        extent = {}
        for option in options:
            extent[option[0]] = [float(el) for el in option[1:]]

        return extent

    def get_border(self, nPoints=10):
        """Generate two vectors with values of lat/lon for the border of domain

        Parameters
        -----------
        nPoints : int, optional
            Number of points on each border

        Returns
        --------
        lonVec, latVec : lists
            vectors with lon/lat values for each point at the border

        """
        n_points = nPoints
        x_size, y_size = self.shape()[::-1]
        x_rc_vec = Domain._get_row_col_vector(x_size, n_points)
        y_rc_vec = Domain._get_row_col_vector(y_size, n_points)
        col_vec, row_vec = Domain._compound_row_col_vectors(x_size, y_size, x_rc_vec, y_rc_vec)
        return self.transform_points(col_vec, row_vec)

    @staticmethod
    def _compound_row_col_vectors(x_size, y_size, x_vec, y_vec):
        col_vec = (x_vec + [x_size] * len(y_vec) + x_vec[::-1] + [0] * len(y_vec))
        row_vec = ([0] * len(x_vec) + y_vec + [y_size] * len(x_vec) + y_vec[::-1])
        return col_vec, row_vec

    @staticmethod
    def _get_row_col_vector(raster_size, n_points):
        step = max(1, raster_size / n_points)
        rc_vec = range(0, raster_size, step)[0:n_points]
        rc_vec.append(raster_size)
        return rc_vec

    def get_border_wkt(self, *args, **kwargs):
        """Creates string with WKT representation of the border polygon

        Returns
        --------
        WKTPolygon : string
            string with WKT representation of the border polygon

        """
        lon_vec, lat_vec = self.get_border(*args, **kwargs)

        ''' The following causes erratic geometry when using
        WKTReader().read(n.get_border_wkt(nPoints=1000)) - only commented out
        now since this may cause other problems...
        '''
        warnings.warn("> 180 deg correction to longitudes - disabled..")
        polygon_border = ','.join('%s %s' % (lon, lat) for lon, lat in zip(lon_vec, lat_vec))
        # outer quotes have to be double and inner - single!
        # wktPolygon = "PolygonFromText('POLYGON((%s))')" % polyCont
        wkt = 'POLYGON((%s))' % polygon_border
        return wkt

    def get_border_geometry(self, *args, **kwargs):
        """ Get OGR Geometry of the border Polygon

        Returns
        -------
        OGR Geometry, type Polygon

        """

        return ogr.CreateGeometryFromWkt(self.get_border_wkt(*args, **kwargs))

    def overlaps(self, anotherDomain):
        """ Checks if this Domain overlaps another Domain

        Returns
        -------
        overlaps : bool
            True if Domains overlaps, False otherwise

        """

        return self.get_border_geometry().Intersects(anotherDomain.get_border_geometry())

    def contains(self, anotherDomain):
        """ Checks if this Domain fully covers another Domain

        Returns
        -------
        contains : bool
            True if this Domain fully covers another Domain, False otherwise

        """

        return self.get_border_geometry().Contains(anotherDomain.get_border_geometry())

    def get_border_postgis(self):
        """ Get PostGIS formatted string of the border Polygon

        Returns
        -------
        str : 'PolygonFromText(PolygonWKT)'

        """

        return "PolygonFromText('%s')" % self.get_border_wkt()

    def get_corners(self):
        """Get coordinates of corners of the Domain

        Returns
        --------
        lonVec, latVec : lists
            vectors with lon/lat values for each corner

        """

        col_vec = [0, 0, self.vrt.dataset.RasterXSize, self.vrt.dataset.RasterXSize]
        row_vec = [0, self.vrt.dataset.RasterYSize, 0, self.vrt.dataset.RasterYSize]
        return self.transform_points(col_vec, row_vec)

    def get_min_max_lat_lon(self):
        """Get minimum and maximum lat and long values in the geolocation grid

        Returns
        --------
        minLat, maxLat, minLon, maxLon : float
            min/max lon/lat values for the Domain

        """
        lon_grd, lat_grd = self.get_geolocation_grids()
        return min(lat_grd[:, 1]), max(lat_grd[:, 1]), min(lon_grd[1, :]), max(lon_grd[1, :])

    def get_pixelsize_meters(self):
        """Returns the pixelsize (deltaX, deltaY) of the domain

        For projected domains, the exact result which is constant
        over the domain is returned.
        For geographic (lon-lat) projections, or domains with no geotransform,
        the haversine formula is used to calculate the pixel size
        in the center of the domain.
        Returns
        --------
        deltaX, deltaY : float
        pixel size in X and Y directions given in meters
        """

        srs = osr.SpatialReference(self.vrt.dataset.GetProjection())
        if srs.IsProjected:
            if srs.GetAttrValue('unit') == 'metre':
                geoTransform = self.vrt.dataset.GetGeoTransform()
                delta_x = abs(geoTransform[1])
                delta_y = abs(geoTransform[5])
                return delta_x, delta_y

        # Estimate pixel size in center of domain using haversine formula
        center_col = round(self.vrt.dataset.RasterXSize/2)
        center_row = round(self.vrt.dataset.RasterYSize/2)
        # TODO: Bad names
        lon00, lat00 = self.transform_points([center_col], [center_row])
        lon01, lat01 = self.transform_points([center_col], [center_row + 1])
        lon10, lat10 = self.transform_points([center_col + 1], [center_row])

        delta_x = haversine(lon00, lat00, lon01, lat01)
        delta_y = haversine(lon00, lat00, lon10, lat10)
        return delta_x[0], delta_y[0]

    @staticmethod
    def _get_geotransform(extent_dict):
        """
        the new coordinates and raster size are calculated based on
        the given extentDic.

        Parameters
        -----------
        extent_dict : dictionary
            includes 'te' key and 'ts' or 'tr' key

        Raises
        -------
        OptionError : occurs when maxX - minX < 0 or maxY - minY < 0

        Returns
        --------
        coordinate : list with 6 float
            GeoTransform

        raster_x_size and raster_y_size

        """
        width = extent_dict['te'][2] - extent_dict['te'][0]
        height = extent_dict['te'][3] - extent_dict['te'][1]

        if width <= 0 or height <= 0:
            raise OptionError('The extent is illegal "-te xMin yMin xMax yMax"')

        if 'tr' in extent_dict.keys():
            resolution_x, resolution_y, raster_x_size, raster_y_size = \
                Domain._transform_tr(width, height, extent_dict['tr'])
        else:
            resolution_x, resolution_y, raster_x_size, raster_y_size = \
                Domain._transform_ts(width, height, extent_dict['ts'])

        # create a list for GeoTransform
        coordinates = [extent_dict['te'][0], resolution_x, 0.0,
                       extent_dict['te'][3], 0.0, resolution_y]

        return coordinates, int(raster_x_size), int(raster_y_size)

    @staticmethod
    def _transform_tr(width, height, tr_arr):
        """
        Calculate X and Y resolution and raster sizes from the "-tr" parameter

        Parameters
        -----------
        width : float, width of domain calculated from the "-te" extent parameter
        height: float, height of domain calculated from the "-te" extent parameter
        tr_arr: list, [<x_resolution>, <y_resolution>]

        Raises
        -------
        OptionError : occurs when the given resolution is larger than width or height.

        Returns
        --------
        resolution_x, resolution_y, raster_x_size, raster_y_size : float
        """
        resolution_x = tr_arr[0]
        # TODO: Review requested, falsification of negative value in resolution_y
        resolution_y = -(tr_arr[1])

        if width < resolution_x or height < resolution_y:
            raise OptionError('"-tr" is too large. width is %s, height is %s ' % (width, height))

        raster_x_size = width / resolution_x
        raster_y_size = abs(height / resolution_y)

        return resolution_x, resolution_y, raster_x_size, raster_y_size

    @staticmethod
    def _transform_ts(width, height, ts_arr):
        raster_x_size, raster_y_size = ts_arr
        resolution_x = width / raster_x_size
        resolution_y = -abs(height / raster_y_size)

        return resolution_x, resolution_y, raster_x_size, raster_y_size

    def transform_points(self, colVector, rowVector, DstToSrc=0, dstSRS=NSR()):

        """Transform given lists of X,Y coordinates into lon/lat or inverse

        Parameters
        -----------
        colVector : lists
            X and Y coordinates in pixel/line or lon/lat  coordinate system
        DstToSrc : 0 or 1
            0 - forward transform (pix/line => lon/lat)
            1 - inverse transformation
        dstSRS : NSR
            destination spatial reference
            
        Returns
        --------
        X, Y : lists
            X and Y coordinates in lon/lat or pixel/line coordinate system

        """
        return self.vrt.transform_points(colVector, rowVector, DstToSrc, dstSRS=dstSRS)

    def azimuth_y(self, reductionFactor=1):
        """Calculate the angle of each pixel position vector with respect to
        the Y-axis (azimuth).

        In general, azimuth is the angle from a reference vector (e.g., the
        direction to North) to the chosen position vector. The azimuth
        increases clockwise from direction to North.
        http://en.wikipedia.org/wiki/Azimuth

        Parameters
        -----------
        reductionFactor : integer
            factor by which the size of the output array is reduced

        Returns
        -------
        azimuth : numpy array
            Values of azimuth in degrees in range 0 - 360

        """

        lon_grd, lat_grd = self.get_geolocation_grids(reductionFactor)
        a = initial_bearing(lon_grd[1:, :], lat_grd[1:, :], lon_grd[:-1:, :], lat_grd[:-1:, :])
        # Repeat last row once to match size of lon-lat grids
        a = np.vstack((a, a[-1, :]))
        return a

    def shape(self):
        """Return Numpy-like shape of Domain object (ySize, xSize)

        Returns
        --------
        shape : tuple of two INT
            Numpy-like shape of Domain object (ySize, xSize)

        """
        return self.vrt.dataset.RasterYSize, self.vrt.dataset.RasterXSize

    def write_map(self, outputFileName,
                  lonVec=None, latVec=None, lonBorder=10., latBorder=10.,
                  figureSize=(6, 6), dpi=50, projection='cyl', resolution='c',
                  continetsColor='coral', meridians=10, parallels=10,
                  pColor='r', pLine='k', pAlpha=0.5, padding=0.,
                  merLabels=[False, False, False, False],
                  parLabels=[False, False, False, False],
                  pltshow=False,
                  labels=None):
        """Create an image with a map of the domain

        Uses Basemap to create a World Map
        Adds a semitransparent patch with outline of the Domain
        Writes to an image file

        Parameters
        -----------
        outputFileName : string
            name of the output file name
        lonVec : [floats] or [[floats]]
            longitudes of patches to display
        latVec : [floats] or [[floats]]
            latitudes of patches to display
        lonBorder : float
            10, horisontal border around patch (degrees of longitude)
        latBorder : float
            10, vertical border around patch (degrees of latitude)
        figureSize : tuple of two integers
            (6, 6), size of the generated figure in inches
        dpi: int
            50, resolution of the output figure (size 6,6 and dpi 50
            produces 300 x 300 figure)
        projection : string, one of Basemap projections
            'cyl', projection of the map
        resolution : string, resolution of the map
            'c', crude
            'l', low
            'i', intermediate
            'h', high
            'f', full
        continetsColor : string or any matplotlib color representation
            'coral', color of continets
        meridians : int
            10, number of meridians to draw
        parallels : int
            10, number of parallels to draw
        pColor : string or any matplotlib color representation
            'r', color of the Domain patch
        pLine : string or any matplotlib color representation
            'k', color of the Domain outline
        pAlpha : float 0 - 1
            0.5, transparency of Domain patch
        padding : float
            0., width of white padding around the map
        merLabels : list of 4 booleans
            where to put meridian labels, see also Basemap.drawmeridians()
        parLables : list of 4 booleans
            where to put parallel labels, see also Basemap.drawparallels()
        labels : list of str
            labels to print on top of patches
        """
        if not BASEMAP_LIB_EXISTS:
            raise ImportError(' Basemap is not installed. Cannot use Domain.write_map. '
                              ' Enable by: conda install -c conda forge basemap ')

        # if lat/lon vectors are not given as input
        if lonVec is None or latVec is None or len(lonVec) != len(latVec):
            lonVec, latVec = self.get_border()

        # convert vectors to numpy arrays
        lonVec = np.array(lonVec)
        latVec = np.array(latVec)

        # estimate mean/min/max values of lat/lon of the shown area
        # (real lat min max +/- latBorder) and (real lon min max +/- lonBorder)
        minLon = max(-180, lonVec.min() - lonBorder)
        maxLon = min(180, lonVec.max() + lonBorder)
        minLat = max(-90, latVec.min() - latBorder)
        maxLat = min(90, latVec.max() + latBorder)
        meanLon = lonVec.mean()
        meanLat = latVec.mean()

        # generate template map (can be also tmerc)
        plt.figure(num=1, figsize=figureSize, dpi=dpi)
        bmap = Basemap(projection=projection,
                       lat_0=meanLat, lon_0=meanLon,
                       llcrnrlon=minLon, llcrnrlat=minLat,
                       urcrnrlon=maxLon, urcrnrlat=maxLat,
                       resolution=resolution)

        # add content: coastline, continents, meridians, parallels
        bmap.drawcoastlines()
        bmap.fillcontinents(color=continetsColor)
        bmap.drawmeridians(np.linspace(minLon, maxLon, meridians),
                           labels=merLabels, fmt='%2.1f')
        bmap.drawparallels(np.linspace(minLat, maxLat, parallels),
                           labels=parLabels, fmt='%2.1f')

        # convert input lat/lon vectors to arrays of vectors with one row
        # if only one vector was given
        if len(lonVec.shape) == 1:
            lonVec = [lonVec]
            latVec = [latVec]

        for i in range(len(lonVec)):
            # convert lat/lons to map units
            mapX, mapY = bmap(list(lonVec[i].flat), list(latVec[i].flat))

            # from x/y vectors create a Patch to be added to map
            boundary = Polygon(zip(mapX, mapY),
                               alpha=pAlpha, ec=pLine, fc=pColor)

            # add patch to the map
            plt.gca().add_patch(boundary)
            plt.gca().set_aspect('auto')

            if labels is not None and labels[i] is not None:
                plt.text(np.mean(mapX), np.mean(mapY), labels[i],
                         va='center', ha='right', alpha=0.5, fontsize=10)

        # save figure and close
        plt.savefig(outputFileName, bbox_inches='tight',
                    dpi=dpi, pad_inches=padding)
        if pltshow:
            plt.show()
        else:
            plt.close('all')

# TODO: rename vrt.reproject_GCPs for disambiguation

    def reproject_GCPs(self, srsString=''):
        '''Reproject all GCPs to a new spatial reference system

        Necessary before warping an image if the given GCPs
        are in a coordinate system which has a singularity
        in (or near) the destination area (e.g. poles for lonlat GCPs)

        Parameters
        ----------
        srsString : string
            SRS given as Proj4 string. If empty '+proj=stere' is used

        Modifies
        --------
            Reprojects all GCPs to new SRS and updates GCPProjection
        '''
        if srsString == '':
            lon, lat = self.get_border()
            srsString = '+proj=stere +datum=WGS84 +ellps=WGS84 +lat_0=%f +lon_0=%f +no_defs'%(
            np.nanmedian(lat), np.nanmedian(lon)) 
        
        
        self.vrt.reproject_GCPs(srsString)
