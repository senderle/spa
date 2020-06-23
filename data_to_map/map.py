import math

from collections import defaultdict, Counter

# from shapely.geometry import Point, Polygon
import pandas
import geopandas as gpd
import shapely
from bokeh.io import show, output_file
from bokeh.models import (
    LinearColorMapper, Circle, MultiPolygons,
    ColumnDataSource, GeoJSONDataSource,
    HoverTool, TapTool, OpenURL
)
from bokeh.palettes import Blues8 as palette
from bokeh.plotting import figure
from bokeh.tile_providers import (
    CARTODBPOSITRON_RETINA,
    get_provider
)


def lat_lon_to_web_mercator(lon, lat):
    x = lon * 20037508.34 / 180
    y = math.log(math.tan((90 + lat) * math.pi / 360)) / (math.pi / 180)
    y = y * 20037508.34 / 180
    return x, y


def polygon_to_list(poly):
    shape = [list(poly.exterior.coords)]
    shape.extend(list(i.coords) for i in poly.interiors)
    return shape


def multipolygons_to_xs_ys(multipolygons):
    geometries = []
    for m in multipolygons:
        if isinstance(m, shapely.geometry.Polygon):
            m = [m]
        else:
            m = list(m)
        geometries.append(list(map(polygon_to_list, m)))

    geo_xs = [[[[x for x, y in ring_pairs]
                for ring_pairs in polygon]
               for polygon in multipolygon]
              for multipolygon in geometries]
    geo_ys = [[[[y for x, y in ring_pairs]
                for ring_pairs in polygon]
               for polygon in multipolygon]
              for multipolygon in geometries]
    return geo_xs, geo_ys


# If the world were a good place, this function would not be
# needed, and we could pass the geopandas dataframe straight
# to GeoJSONDataSource. That ALMOST works. But for some
# reason, no existing Bokeh glyph understands how to render
# patches with holes in them as represented by shapely Polygons.
# The closest thing is Bokeh's MultiPolygons glyph, but it
# doesn't accept shapely objects or geojson or anything
# like that. Wah wah. So instead we have to do this by hand.
# Also, Infinity isn't a valid JSON value, even though pandas
# seems to think it is.
def geodf_patches_to_geods(geodf):
    geo_xs, geo_ys = multipolygons_to_xs_ys(geodf['geometry'])
    geodf = geodf.assign(xs=geo_xs, ys=geo_ys)
    return GeoJSONDataSource(
        geojson=geodf.to_json().replace('Infinity', 'null')
    )


def safe_lt(comp):
    def comp_func(val):
        try:
            return float(val) < comp
        except ValueError:
            return False
    return comp_func


def can_be_simplified(val, tol=10.0):
    try:
        val.simplify(tol)
        return True
    except (ValueError, AttributeError):
        return False


def load_geojson(simplify_tol=None):
    gdf = gpd.read_file('data/gadm28_countries.geojson')
    gdf = gdf[gdf['geometry'].notna()]

    # For all countries, drop just Antarctica:
    # gdf = gdf[gdf['name_engli'] != 'Antarctica']

    # For just africa drop other continents:
    gdf = gdf[gdf['unregion2'] == 'Africa']

    gdf['name'] = gdf['name_engli']
    gdf = gdf.set_index('name_engli')

    # Project from lat, lon data to web mercator.
    gdf = gdf.to_crs('EPSG:3857')

    # Use shapely simplification routine if simplify_tol is specified.
    if simplify_tol is not None:
        gdf = gdf[gdf['geometry'].apply(can_be_simplified)]
        gdf['geometry'] = gdf['geometry'].simplify(
            simplify_tol,
            preserve_topology=False
        )
    return gdf


def load_protests():
    protests = pandas.read_csv('protests.csv')
    protests_wrong_data = protests[
        (protests.LONG == 'checked') | (protests.LONG.apply(safe_lt(-20))) |
        (protests.LONG.isna()) | (protests.LAT.isna())
    ]
    protests = protests.drop(protests_wrong_data.index, axis='rows')
    protests['LAT'] = protests.LAT.apply(float)
    protests['LONG'] = protests.LONG.apply(float)
    protests = protests[~((protests.LAT == 0) & (protests.LONG == 0))]

    protests = gpd.GeoDataFrame(
        protests,
        geometry=gpd.points_from_xy(protests.LONG, protests.LAT),
        crs='epsg:4326'  # CRS code for basic lat/lon data.
    )
    protests = protests.to_crs('EPSG:3857')  # CRS code for web mercator.
    return protests


def load_protest_reverse():
    try:
        return pandas.read_csv('protest-reverse-cache.csv')
    except FileNotFoundError:
        pass


def save_protest_reverse(data):
    keys = list(set(k for row in data for k in row.keys()))
    rows = [{k: row.get(k, None) for k in keys} for row in data]
    df = pandas.DataFrame({k: [r[k] for r in rows] for k in keys})
    df.to_csv('protest-reverse-cache.csv')


_name_errors = {
    'Madagascar ': 'Madagascar',
    "Cote d'lvoire": "Côte d'Ivoire",
    'Djbouti': 'Djibouti',
    'Malawi ': 'Malawi',
    'Mauritus': 'Mauritius',
    'Mauritania ': 'Mauritania',
    'Congo- Brazzaville': 'Republic of Congo',
    'Congo - Kinshasa': 'Democratic Republic of the Congo',
    'Guinea Bissau': 'Guinea-Bissau'
}


def sum_protests(protests, nations):
    counts = defaultdict(int)

    names = [_name_errors[n] if n in _name_errors else n
             for n in protests.Name]
    counts = Counter(names)

    print(set(counts) - set(nations['name']))
    print(set(nations['name']) - set(counts))

    nations['protestcount'] = [counts[n] for n in nations['name']]

    nation_rank = sorted(set(counts.values()), reverse=True)
    nation_rank.append(0)
    nation_rank = {c: i for i, c in enumerate(nation_rank)}
    nation_rank = {n: nation_rank[counts[n]] for n in nations['name']}
    nations['rank'] = [nation_rank[n] for n in nations['name']]


def base_map():
    TOOLS = "pan,wheel_zoom,tap,reset,save"

    # Plot
    p = figure(
        title="Protests", tools=TOOLS,
        active_scroll='wheel_zoom',
        x_axis_location=None, y_axis_location=None,
        x_range=(-2300000, 6300000), y_range=(-4300000, 4600000),
        x_axis_type="mercator", y_axis_type="mercator",
        )
    p.toolbar_location = None

    # tile_provider = get_provider(STAMEN_TONER)
    # tile_provider.url = ('http://tile.stamen.com/toner-lite/'
    #                      '{Z}/{X}/{Y}@2x.png')
    tile_provider = get_provider(CARTODBPOSITRON_RETINA)
    # tile_provider.url = ('https://tiles.basemaps.cartocdn.com/'
    #                      'light_only_labels/{z}/{x}/{y}@2x.png')
    p.add_tile(tile_provider)
    p.grid.grid_line_color = None

    return p


def patches(plot, patch_data):
    color_mapper = LinearColorMapper(palette=palette)
    patches = MultiPolygons(
        xs='xs', ys='ys',
        fill_color={'field': 'rank', 'transform': color_mapper},
        fill_alpha=0.5, line_color="lightblue", line_alpha=0.3,
        line_width=3.0
    )
    hover_patches = MultiPolygons(
        xs='xs', ys='ys',
        fill_color={'field': 'rank', 'transform': color_mapper},
        fill_alpha=0.5, line_color="purple", line_alpha=0.8,
        line_width=3.0
    )
    render = plot.add_glyph(geodf_patches_to_geods(patch_data),
                            patches,
                            hover_glyph=hover_patches,
                            selection_glyph=patches,
                            nonselection_glyph=patches)
    plot.add_tools(HoverTool(
        # tooltips=[
        #     ("Country", "@name"),
        #     ("Number of Protests", "@protestcount"),
        # ],
        tooltips=None,
        renderers=[render],
        point_policy="follow_mouse"
    ))
    tap = plot.select_one(TapTool)
    tap.renderers = [render]
    tap.callback = OpenURL(
        url='https://wikipedia.com/wiki/@name{safe}'
    )
    return plot


def points(plot, point_data):
    point = Circle(x='x', y='y', fill_color="purple", fill_alpha=0.5,
                   line_color="gray", line_alpha=0.5, size=6)
    plot.add_glyph(GeoJSONDataSource(geojson=point_data.to_json()), point)


if __name__ == "__main__":
    plot = base_map()

    protests = load_protests()
    nations = load_geojson()
    sum_protests(protests, nations)

    patches(plot, nations)
    points(plot, protests)

    output_file("index.html")
    show(plot)
