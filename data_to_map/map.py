import math
import os
import re
import time
import json
import sys
import signal
from pathlib import Path
from collections import defaultdict, Counter

# from shapely.geometry import Point, Polygon
import pandas
import geopandas as gpd
import shapely
# from bokeh.io import show, output_file
from bokeh.models import (
    LinearColorMapper,
    Circle,
    MultiPolygons,
    GeoJSONDataSource,
    HoverTool,
    WheelZoomTool,
    PanTool,
    Panel,
    Tabs,
    WMTSTileSource,
    CustomJS,
    Div,
    # MultiSelect,
    MultiChoice,
    # ColumnDataSource,
    # TapTool,
    # OpenURL,
    # CustomJSHover,
)
from bokeh.layouts import column, row
from bokeh.palettes import Blues8 as palette
from bokeh.plotting import figure
from bokeh.resources import JSResources
from bokeh.embed import (
    # file_html,
    components,
)


# Currently unused, but preserved here for reference.
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
    gdf = gpd.read_file('data_to_map/data/gadm28_countries.geojson')
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
    protests = pandas.read_csv('data_to_map/data/protests.csv')
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
             for n in protests['Country Name']]
    counts = Counter(names)

    # print(set(counts) - set(nations['name']))
    # print(set(nations['name']) - set(counts))

    nations['protestcount'] = [counts[n] for n in nations['name']]

    nation_rank = sorted(set(counts.values()), reverse=True)
    nation_rank.append(0)
    nation_rank = {c: i for i, c in enumerate(nation_rank)}
    nation_rank = {n: nation_rank[counts[n]] for n in nations['name']}
    nations['rank'] = [nation_rank[n] for n in nations['name']]


def base_map(tile_url, tile_attribution='MapTiler'):
    # Plot
    p = figure(
        title="",
        plot_width=600, plot_height=700,
        x_axis_location=None, y_axis_location=None,
        y_range=(-4300000, 4600000),
        x_range=(-2450000, 6450000),
        x_axis_type="mercator", y_axis_type="mercator",
        )

    zoom = WheelZoomTool()
    p.add_tools(zoom)
    p.toolbar.active_scroll = zoom

    drag = PanTool()
    p.add_tools(drag)
    p.toolbar.active_drag = drag

    p.toolbar_location = None
    p.grid.grid_line_color = None

    p.add_tile(WMTSTileSource(
        url=tile_url,
        attribution=tile_attribution
    ))

    return p


def patches(plot, div, patch_data):
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
    patch_source = geodf_patches_to_geods(patch_data)
    render = plot.add_glyph(patch_source,
                            patches,
                            hover_glyph=hover_patches,
                            selection_glyph=patches,
                            nonselection_glyph=patches)

    parsed_geojson = json.loads(patch_source.geojson)
    # str.source.selected.indices gives you a list of things that you
    # immediately clicked on
    code = """
        var features = json_source['features'];
        var properties = features[cb_data.index.indices[0]];
        if (properties != undefined) {
            var rank = properties['properties']['rank'] + 1;
            var name = properties['properties']['name'];
            var protestcount = properties['properties']['protestcount'];
            div.text = 'Rank: ' +  rank + '<br>' + 'Name: ' + name +
                       '<br>' + 'Protest Count: ' + protestcount
            }
    """

    callback = CustomJS(
        args=dict(json_source=parsed_geojson, div=div),
        code=code
    )
    hover = HoverTool(
        tooltips=None,
        renderers=[render],
        point_policy="follow_mouse",
        callback=callback
    )
    plot.add_tools(hover)
    plot.toolbar.active_inspect = hover

    '''tap.callback = OpenURL(
        url='https://wikipedia.com/wiki/@name{safe}'
    )'''
    return plot


def points(plot, div, point_source):
    point = Circle(x='x', y='y', fill_color="purple", fill_alpha=0.5,
                   line_color="gray", line_alpha=0.5, size=6, name="points")
    cr = plot.add_glyph(point_source,
                        point,
                        hover_glyph=point,
                        selection_glyph=point,
                        name="points")
    callback = CustomJS(args=dict(source=point_source, div=div),
                        code="""
        var features = source['data'];
        var indices = cb_data.index.indices;

        if (indices.length != 0) {
            div.text = "Number of protests: " + indices.length + "<br>"
            var counter = 0;
            for (var i = 0; i < indices.length; i++) {
                if (counter == 5) {
                    if (indices.length == 6) {
                        div.text = div.text + "<br>" + "<em>" +
                                   "Additional protest not shown" +
                                   "</em>" +  "<br>";
                    } else {
                        div.text = div.text + "<br>" + "<em>" +
                                   "Additional " + (indices.length -5) +
                                   " protests not shown" + "</em>" +  "<br>";
                    }
                    break;
                } else {
                    counter++;
                }
                var protest = indices[i];
                var desc = features['Description of Protest'][protest];
                var uni = features['School Name'][protest];
                var type = features['Event Type (F3)'][protest];
                div.text = div.text + counter + '.' + '<br>' +
                           'Description: ' + desc + '<br>' + ' Location: ' +
                           uni + '<br>' + ' Type of Protest: ' + type +
                           '<br>';
                }
        }
    """)
    hover = HoverTool(
        tooltips=None,
        point_policy="follow_mouse",
        renderers=[cr],
        callback=callback
    )
    plot.add_tools(hover)
    plot.toolbar.active_inspect = hover


def one_filter(plot, point_source, filter_col):
    # Remove (FX) from column name; probaby temporary
    title = re.sub(r'\s*[(]F[0-9]+[)]\s*', '', filter_col)

    full_source = GeoJSONDataSource(geojson=point_source.geojson)

    # Extract options from values in the data:
    options = [f['properties'][filter_col]
               for f in json.loads(point_source.geojson)['features']]
    # Some values are comma-separated lists; split and unpack, dropping None.
    options = [opt.strip()
               for opt_list in options if opt_list is not None
               for opt in opt_list.split(',')]
    # Deduplicate and turn into name-value pairs, as required by MultiSelect.
    options = [(opt,) * 2 for opt in sorted(set(options))]
    multi_select = MultiChoice(
        title=title,
        width=int(plot.plot_width / 1.5),
        height=int(plot.plot_height / 8),
        max_items=4,
        options=options
    )

    callback = CustomJS(
        args=dict(source=point_source,
                  multi_select=multi_select,
                  full_source=full_source,
                  filter_col=filter_col),
        code="""

        function filter(select_vals, source, filter, full_source) {
            for (const [key, value] of Object.entries(source.data)) {
                while (value.length > 0) {
                    value.pop();
                }
            }
            for (const [key, value] of Object.entries(full_source.data)) {
                for (let i = 0; i < value.length; i++) {
                    if (isIncluded(filter, select_vals, i, full_source)) {
                        source.data[key].push(value[i]);
                    }
                }
            }
        }

        function isIncluded(filter, select_vals, index, full_source) {
            // Slow -- O(i * j) -- but i and j never get much larger than 10
            for (var i = 0; i < select_vals.length; i++) {
                let vals = full_source.data[filter][index];
                vals = vals ? vals.split(',').map(s => s.trim()) : [];
                for (let j = 0; j < vals.length; j++) {
                    if (vals[j] == select_vals[i]) {
                        return true;
                    }
                }
            }
            return false;
        }

        var select_vals = cb_obj.value;
        filter(select_vals, source, filter_col, full_source);
        source.change.emit();
        """)
    multi_select.js_on_change('value', callback)
    return multi_select


class Map:
    def __init__(self):
        self.protests = load_protests()
        self.nations = load_geojson()
        self.filters = self.collect_filters()
        sum_protests(self.protests, self.nations)

    def collect_filters(self):
        cols = self.protests.columns
        filters = [f for f in cols if re.search(r'\s*[(]F[0-9]+[)]\s*', f)]
        digits = [int(re.search(r'\s*[(]F(?P<n>[0-9]+)[)]\s*', f)['n'])
                  for f in filters]
        filters = [f for d, f in sorted(zip(digits, filters))]
        return filters

    def patch_plot(self, title, tile_url, tile_attribution='MapTiler'):
        plot = base_map(tile_url, tile_attribution)

        div = Div(width=plot.plot_width // 2,
                  height=plot.plot_height,
                  height_policy="fixed")

        patches(plot, div, self.nations)
        layout = row(plot, div)
        return Panel(child=layout, title=title)

    def point_plot(self, title, tile_url, tile_attribution='MapTiler'):
        plot = base_map(tile_url, tile_attribution)

        div = Div(width=plot.plot_width // 2,
                  height=plot.plot_height,
                  height_policy="fixed")

        point_source = GeoJSONDataSource(geojson=self.protests.to_json())
        points(plot, div, point_source)
        select_col = [one_filter(plot, point_source, filter_name)
                      for filter_name in self.filters]
        select_col = column(*select_col)
        map_select = row(plot, select_col)
        layout = column(map_select, div)
        return Panel(child=layout, title=title)

    def nation_pages(self, path):
        for i, name in enumerate(self.nations.index.values):
            perma = name.lower().replace(' ', '-').replace('\'', '-')
            filename = (Path(path) / Path(perma)).with_suffix('.md')
            title = name
            with open(filename, 'w', encoding='utf-8') as op:
                op.write(f'---\n'
                         f'layout: country\n'
                         f'row_index: {i}\n'
                         f'permalink: {perma}\n'
                         f'title: {title}\n'
                         f'hidden: true\n'
                         f'---\n')

    def protest_pages(self, path):
        for i, name in enumerate(self.protests.index.values):
            perma = name.lower().replace(' ', '-').replace('\'', '-')
            filename = (Path(path) / Path(perma)).with_suffix('.md')
            title = name
            with open(filename, 'w', encoding='utf-8') as op:
                op.write(f'---\n'
                         f'layout: country\n'
                         f'row_index: {i}\n'
                         f'permalink: {perma}\n'
                         f'title: {title}\n'
                         f'hidden: true\n'
                         f'---\n')


def save_embed(plot):
    with open("jekyll/_includes/map.html", 'w', encoding='utf-8') as op:
        save_components(plot, op)

    # This ensures that the right version of BokehJS is always in use
    # on the jekyll site.
    with open('jekyll/_includes/bokeh_heading.html',
              'w', encoding='utf-8') as op:
        save_script_tags(op)


def save_html(plot):
    with open("map-standalone.html", 'w', encoding='utf-8') as op:
        op.write("""
        <!DOCTYPE html>
        <html lang="en">
        """)

        save_script_tags(op)
        save_components(plot, op)

        op.write("""
        <div id="map-hover-context">
        </div>
        </html>
        """)


def save_script_tags(open_file):
    # This loads more JS files than is strictly necessary. We really only
    # need the main bokeh file and the widgets file. But it's not yet clear
    # that the gain in loading time is worth the extra complexity of weeding
    # out the other files.
    for f in JSResources(mode='cdn').js_files:
        open_file.write(
            f'<script type="text/javascript" src="{f}" '
            'crossorigin="anonymous"></script>\n'
        )

    open_file.write(
        '<script type="text/javascript"> \n'
        '    Bokeh.set_log_level("info"); \n'
        '</script>\n'
    )


def save_components(plot, open_file):
    for c in components(plot):
        open_file.write(c)
        open_file.write('\n')


def main(embed=True):
    patch_key = ('https://api.maptiler.com/maps/voyager/{z}/{x}/{y}.png?'
                 'key=k3o6yW6gLuLZpwLM3ecn')
    point_key = ('https://api.maptiler.com/maps/streets/{z}/{x}/{y}.png?'
                 'key=xEyWbUmfIFzRcu729a2M')

    map = Map()
    map.nation_pages('jekyll/_nations')
    vis = Tabs(tabs=[map.patch_plot("Country", patch_key),
                     map.point_plot("Protest", point_key)])
    if embed:
        save_embed(vis)
    else:
        save_html(vis)


if __name__ == "__main__":

    if '--standalone' in sys.argv[1:]:
        print("Generating standalone map...")
        main(embed=False)
    else:
        # Get the default signal handler for SIGTERM (see below)
        default_sigterm = signal.getsignal(signal.SIGTERM)

        # We set these variables to keep track of changes
        temp_time = 0
        recent_time = 0
        print("Watching input directory for changes every ten seconds.")
        while True:
            for data_file in os.listdir("data_to_map/data"):
                mod_time = os.path.getmtime(os.path.join("data_to_map/data",
                                                         data_file))
                if mod_time > recent_time:
                    recent_time = mod_time
            if recent_time > temp_time:
                temp_time = recent_time
                print("Change detected, generating new map...")
                main()
                print("Map generation complete.")
                print("Watching for changes...")

            # Listen for SIGTERM from docker while sleeping.
            signal.signal(signal.SIGTERM, lambda sig, frame: sys.exit(0))
            time.sleep(10)
            # Ignore SIGTERM while working.
            signal.signal(signal.SIGTERM, default_sigterm)

