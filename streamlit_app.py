import geopandas
import pandas
import numpy
import tifffile
import shapely
import streamlit as st
from lmd.lib import SegmentationLoader
from lmd.lib import Collection, Shape
from lmd import tools
from PIL import Image
from pathlib import Path
import ast
import string
import itertools
import random
import json
import time

from loguru import logger
import sys
logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss.SS}</green> | <level>{level}</level> | {message}")

####################
##### Functions ####
####################

def extract_coordinates(geometry):
      if geometry.geom_type == 'Polygon':
         return [list(coord) for coord in geometry.exterior.coords]
      elif geometry.geom_type == 'LineString':
         return [list(coord) for coord in geometry.coords]
      else:
         st.write(f'Geometry type {geometry.geom_type} not supported, please convert to Polygon or LineString in Qupath')
         st.stop()

def generate_combinations(list1, list2, num) -> list:
   """Generate dictionary from all combinations of two lists and a range, assigning arbitrary values."""
   assert isinstance(list1, list) and all(isinstance(i, str) for i in list1), "Input 1 must be a list of strings"
   assert isinstance(list2, list) and all(isinstance(i, str) for i in list2), "Input 2 must be a list of strings"
   assert isinstance(num, int) and num > 0, "Input 3 must be a positive integer"
   keys = [f"{a}_{b}_{i}" for a, b, i in itertools.product(list1, list2, range(1, num + 1))]
   return keys

def create_list_of_acceptable_wells():
   list_of_acceptable_wells =[]
   for row in list(string.ascii_uppercase[2:14]):
      for column in range(3,22):
         list_of_acceptable_wells.append(str(row) + str(column))
   return list_of_acceptable_wells

def create_default_samples_and_wells(list_of_samples, list_of_acceptable_wells):
   assert len(list_of_samples) <= len(list_of_acceptable_wells), "Number of samples must be less than or equal to the number of wells"
   samples_and_wells = {}
   for sample,well in zip(list_of_samples, list_of_acceptable_wells):
      samples_and_wells[sample] = well
   return samples_and_wells

def sample_placement_384wp(samples_and_wells):

   rows_A_P= [i for i in string.ascii_uppercase[:16]]
   columns_1_24 = [str(i) for i in range(1,25)]
   df_wp384 = pandas.DataFrame('',columns=columns_1_24, index=rows_A_P)

   for i in samples_and_wells:
      location = samples_and_wells[i]
      df_wp384.at[location[0],location[1:]] = i

   return df_wp384

def QC_geojson_file(geojson_path: str):
   df = geopandas.read_file(geojson_path)
   logger.info(f"Geojson file loaded with shape {df.shape} for metadata coloring")
   df = df[df['geometry'].apply(lambda geom: not isinstance(geom, shapely.geometry.Point))]
   logger.info(f"Point geometries have been removed")
   df = df[df['classification'].notna()]
   df = df[df.geometry.geom_type != 'MultiPolygon']
   df['classification_name'] = df['classification'].apply(lambda x: ast.literal_eval(x).get('name') if isinstance(x, str) else x.get('name'))
   return df

def parse_metadata_csv(csv_path):
   try:
      df = pandas.read_csv(csv_path, sep=None, engine="python", encoding="utf-8-sig")
      return df
   except Exception as e:
      logger.error(f"Error reading CSV file: {e}")
      st.stop()
   
def check_ids(shapes, metadata, metadata_name_key):
   logger.debug("Running Check IDS")
   # remove whitespaces
   metadata[metadata_name_key] = metadata[metadata_name_key].astype(str).str.strip()
   logger.debug("metadata names have been stripped")
   logger.debug(f"metadata shape {metadata.shape}")   

   shape_names_set = set(shapes['classification_name'])
   metadata_names_set = set(metadata[metadata_name_key])
   logger.debug(f"First 5 shape names: {list(shape_names_set)[:5]}")
   logger.debug(f"First 5 metadata names: {list(metadata_names_set)[:5]}")

   # all shapes must be in metadata
   if shape_names_set.issubset(metadata_names_set): 
      logger.info("All shape names are found in the metadata")
      st.write("All shape names are found in the metadata")
      return True
   else:
      logger.error(f"{shape_names_set - metadata_names_set} were not found in the metadata")
      logger.error(f"overlapping names: {shape_names_set & metadata_names_set}")
      return False

def process_geojson_with_metadata(path_to_geojson, path_to_csv, metadata_name_key, metadata_variable_key):

   shapes = QC_geojson_file(geojson_path=path_to_geojson)
   metadata = parse_metadata_csv(path_to_csv)
   
   assert check_ids(shapes=shapes, metadata=metadata, metadata_name_key=metadata_name_key)
   
   #add info to shapes
   mapping = dict(zip(metadata[metadata_name_key], metadata[metadata_variable_key])) #assumes class name in first column
   shapes["metadata"] = shapes["classification_name"].map(mapping)
   
   #format info to QuPath readable way
   default_colors = [[31, 119, 180], [255, 127, 14], [44, 160, 44], [214, 39, 40], [148, 103, 189]]
   color_cycle = itertools.cycle(default_colors)
   color_dict = dict(zip(metadata[metadata_variable_key].astype("category").cat.categories.astype(str), color_cycle))
   shapes['classification'] = shapes["metadata"].apply(lambda x: {'name': x, 'color': color_dict[x]})
   shapes['name'] = shapes['classification_name']
   shapes.drop(columns=["classification_name", "metadata", "id"], inplace=True)

   #export
   shapes.to_file(f"./{path_to_geojson.name.replace("geojson", metadata_variable_key + "_labelled_shapes.geojson")}", driver= "GeoJSON")

@st.cache_data
def load_and_QC_geojson_file(geojson_path: str, list_of_calibpoint_names: list = ['calib1','calib2','calib3']):
   """
   This function loads a geojson file and checks for common issues that might arise when converting it to xml for LMD

   Parameters:
   geojson_path (str): path to the geojson file
   list_of_calibpoint_names (list): list of calibration point names in the geojson file

   Returns:
   None

   Latest update: 29.04.2024 by Jose Nimo
   """

   #load geojson file
   df = geopandas.read_file(geojson_path)
   logger.info(f"Geojson file loaded with shape {df.shape}")
   
   try:
      df['annotation_name'] = df['name']
   except:
      logger.warning('No name column found, meaning no annotation in Qupath was named, at least calibration points should be named')
      st.stop()

   geometry_counts = df.geometry.geom_type.value_counts()
   log_message = ", ".join(f"{count} {geom_type}s" for geom_type, count in geometry_counts.items())
   logger.info(f"Geometries in DataFrame: {log_message}")
   st.write(f"Geometries in DataFrame: {log_message}")

   #check for calibration points
   for point_name in list_of_calibpoint_names:
      if point_name not in df['annotation_name'].unique():
         logger.error(f'Your given annotation_name {point_name} is not present in the file')
         logger.error(f'These are the calib points you passed: {list_of_calibpoint_names}')
         logger.error(f"These are the calib points found in the geojson you gave me: {df[df['geometry'].geom_type == 'Point']['annotation_name']}")

   calib_np_array = numpy.array(
      [[ df.loc[df['name'] == point_name, 'geometry'].values[0].x,
         df.loc[df['name'] == point_name, 'geometry'].values[0].y] 
         for point_name in list_of_calibpoint_names])
   
   def polygon_intersects_triangle(polygon, triangle):
      if isinstance(polygon, shapely.Polygon):
         return polygon.intersects(triangle)
      elif isinstance(polygon, shapely.LineString):
         return polygon.intersects(triangle)
      else:
         return False  # Return False for other geometry types

   df['intersects'] = df['geometry'].apply(lambda x: polygon_intersects_triangle(x, shapely.Polygon(calib_np_array)))
   num_of_polygons_and_LineString = df[df['geometry'].geom_type.isin(['Polygon', 'LineString'])].shape[0]

   intersect_fraction = df['intersects'].sum()/num_of_polygons_and_LineString
   logger.info(f" {intersect_fraction*100:.2f}% of polygons are within calibration triangle")
   st.write(f" {intersect_fraction*100:.2f}% of polygons are within calibration triangle")

   if intersect_fraction < 0.25:
      st.write('WARNING: Less than 25% of the objects intersect with the calibration triangle')
      logger.warning(f"Less than 25% of the objects intersect with the calibration triangle")
      logger.warning(f"Polygons will most likely be warped, consider changing calib points")
   
   #remove points
   df = df[df['geometry'].apply(lambda geom: not isinstance(geom, shapely.geometry.Point))]
   logger.info(f"Point geometries have been removed")

   #check and remove empty classifications
   if df['classification'].isna().sum() !=0 :
      st.write(f"you have {df['classification'].isna().sum()} NaNs in your classification column",
            "these are unclassified objects from Qupath, they will be ignored") 
      df = df[df['classification'].notna()]
   
   #get classification name from inside geometry properties   
   df['classification_name'] = df['classification'].apply(lambda x: ast.literal_eval(x).get('name') if isinstance(x, str) else x.get('name'))
   
   #check for MultiPolygon objects
   if 'MultiPolygon' in df.geometry.geom_type.value_counts().keys():
      st.write('MultiPolygon objects present:  \n')
      st.table(df[df.geometry.geom_type == 'MultiPolygon'][['annotation_name','classification_name']])
      st.write('these are not supported, please convert them to polygons in Qupath  \n',
      'the script will continue but these objects will be ignored')
      df = df[df.geometry.geom_type != 'MultiPolygon']

   df['coords'] = df.geometry.simplify(1).apply(extract_coordinates)

   st.success('The file QC is complete')

@st.cache_data
def load_and_QC_SamplesandWells(geojson_path, list_of_calibpoint_names, samples_and_wells_input):

   df = geopandas.read_file(geojson_path)
   df = df[df['name'].isin(list_of_calibpoint_names) == False]
   df = df[df['classification'].notna()]
   df = df[df.geometry.geom_type != 'MultiPolygon']

   df['Name'] = df['classification'].apply(lambda x: ast.literal_eval(x).get('name') if isinstance(x, str) else x.get('name'))

   samples_and_wells_processed = samples_and_wells_input.replace("\n", "")
   samples_and_wells_processed = samples_and_wells_processed.replace(" ", "")
   samples_and_wells = ast.literal_eval(samples_and_wells_processed)

   list_of_acceptable_wells = create_list_of_acceptable_wells()

   logger.debug("Checking if the wells are in the list of acceptable wells")
   warning_limit = 0
   for well in samples_and_wells.values():
      if well not in list_of_acceptable_wells:
         warning_limit +=1
         if warning_limit<10:
            st.write(f'Your well {well} is not in the list of acceptable wells for 384 well plate, please correct it')

   if warning_limit>=10:
      st.write(f'You have received +10 warnings about wells, I let you be, but I hope you know what you are doing!')

   logger.debug("Checking if the names of geometries are in the samples_and_wells dictionary")
   for name in df.Name.unique():
      if name not in samples_and_wells.keys():
         st.write(f'Your name "{name}" is not in the list of samples_and_wells',
         'Option A: change the class name in Qupath',
         'Option B: add it to the samples_and_wells dictionary',
         'Option C: ignore this, and these annotations will not be exported')
         # st.stop(), let users know, but don't stop the script

   st.success('The samples and wells scheme QC is done!')

@st.cache_data
def create_collection(geojson_path, list_of_calibpoint_names, samples_and_wells_input):

   df = geopandas.read_file(geojson_path)

   calib_np_array = numpy.array(
      [[ df.loc[df['name'] == point_name, 'geometry'].values[0].x,
         df.loc[df['name'] == point_name, 'geometry'].values[0].y] 
         for point_name in list_of_calibpoint_names])

   df = df[df['geometry'].apply(lambda geom: not isinstance(geom, shapely.geometry.Point))]
   df = df[df['classification'].notna()]
   df = df[df.geometry.geom_type != 'MultiPolygon']
   
   df['coords'] = df.geometry.simplify(1).apply(extract_coordinates)
   df['Name'] = df['classification'].apply(lambda x: ast.literal_eval(x).get('name') if isinstance(x, str) else x.get('name'))

   samples_and_wells_processed = samples_and_wells_input.replace("\n", "")
   samples_and_wells_processed = samples_and_wells_processed.replace(" ", "")
   samples_and_wells = ast.literal_eval(samples_and_wells_processed)
   
   #filter out shapes from df that are not in samples_and_wells
   df = df[df['Name'].isin(samples_and_wells.keys())]

   the_collection = Collection(calibration_points = calib_np_array)
   the_collection.orientation_transform = numpy.array([[1,0 ], [0,-1]])
   for i in df.index:
      the_collection.new_shape(df.at[i,'coords'], well = samples_and_wells[df.at[i, "Name"]])

   the_collection.plot(save_name= "./TheCollection.png")
   st.image("./TheCollection.png", caption='Your Contours', use_column_width=True)
   st.write(the_collection.stats())
   the_collection.save(f'./{uploaded_file.name.replace("geojson", "xml")}')
   
   df_wp384 = sample_placement_384wp(samples_and_wells)
   df_wp384.to_csv(f'./{uploaded_file.name.replace("geojson", "_384_wellplate.csv")}', index=True)

####################
### Introduction ###
####################
st.markdown("""
            # Convert a GeoJSON polygons for Laser Microdissection
            ## Part of the [openDVP](https://github.com/CosciaLab/openDVP) framework
            ### For help, post issue on [Github](https://github.com/CosciaLab/Qupath_to_LMD) with .geojson file
            """)
st.divider()


############################
## Step 1: Geojson upload ##
############################

st.markdown("""
            ## Step 1: Upload and check .geojson file from Qupath
            Upload your .geojson file from qupath, order of calibration points is important
            """)

uploaded_file = st.file_uploader("Choose a file", accept_multiple_files=False)
calibration_point_1 = st.text_input("Enter the name of the first calibration point: ",  placeholder ="first_calib")
calibration_point_2 = st.text_input("Enter the name of the second calibration point: ", placeholder ="second_calib")
calibration_point_3 = st.text_input("Enter the name of the third calibration point: ",  placeholder ="third_calib")
list_of_calibpoint_names = [calibration_point_1, calibration_point_2, calibration_point_3]

if st.button("Load and check the geojson file"):
   if uploaded_file is not None:
      load_and_QC_geojson_file(geojson_path=uploaded_file, list_of_calibpoint_names=list_of_calibpoint_names)
   else:
      st.warning("Please upload a file first.")
st.divider()


######################################
## Step 2: Samples and wells upload ##
######################################

st.markdown("""
            ## Step 2: Copy/Paste and check samples and wells scheme
            Sample names will be checked against the uploaded geojson file.  
            Using default is **not** possible, I am nudging users to save their samples_and_wells.  

            Samples and wells have this pattern:
            ```python  
            {"sample_1" : "C3",  
            "sample_2" : "C5",  
            "sample_3" : "C7"}  
            ```

            If you have many samples and this is very laborious go to Step 5, I offer you a shortcut :)  
            """)

samples_and_wells_input = st.text_area("Enter the desired samples and wells scheme")

if st.button("Check the samples and wells"):
   samples_and_wells = load_and_QC_SamplesandWells(samples_and_wells_input=samples_and_wells_input, 
                                                   geojson_path=uploaded_file, 
                                                   list_of_calibpoint_names=list_of_calibpoint_names)
st.divider()

###############################
### Step 3: Process contours ##
###############################

st.markdown("""
            ## Step 3: Process to create .xml file for LMD
            Here we create the .xml file from your geojson.  
            Please download the QC image, and plate scheme for future reference.  
            """)

if st.button("Process geojson and create the contours"):
   create_collection(geojson_path=uploaded_file,
                     list_of_calibpoint_names=list_of_calibpoint_names,
                     samples_and_wells_input=samples_and_wells_input)
   st.success("Contours created successfully!")
   st.download_button("Download contours file", 
                     Path(f'./{uploaded_file.name.replace("geojson", "xml")}').read_text(), 
                     f'./{uploaded_file.name.replace("geojson", "xml")}')
   st.download_button("Download 384 plate scheme", 
                     Path(f'./{uploaded_file.name.replace("geojson", "_384_wellplate.csv")}').read_text(),
                     f'./{uploaded_file.name.replace("geojson", "_384_wellplate.csv")}')
st.divider()


#######################
####### EXTRAS ########
#######################

st.markdown("""
            # Extras to make your life easier :D
             - Create Qupath classes
             - Create default samples and wells
             - Color shapes with categorical
            """)
st.divider()


#################################
## EXTRA 1: Classes for QuPath ##
#################################

st.markdown("""
            ## Extra #1 : Create QuPath classes from categoricals
            Creating many QuPath classes can be tedious, and is very error prone, especially for large projects.  
            This tool takes in two lists of categoricals, and a number for replicates, and create a class for every permutation.  
            
            Afterwards you must:
            1. Create a new QuPath project 
            2. Close QuPath window
            3. Delete `<QuPath project>/classifiers/annotations/classes.json`
            4. Replace with newly created file
            5. Rename it as `classes.json`
            6. Reopen QuPath with project, and you should see classes
            """)


color_map = {"red": 0xFF0000,"green": 0x00FF00,"blue": 0x0000FF,
            "magenta": 0xFF00FF,"cyan": 0x00FFFF,"yellow": 0xFFFF00}
java_colors = [-(0x1000000 - rgb) for rgb in color_map.values()]

input1 = st.text_area("Enter first categorical (comma-separated)", placeholder="example: celltype_A, celltype_B")
input2 = st.text_area("Enter second categorical (comma-separated)", placeholder="example: control, drug_treated")
input3 = st.number_input("Enter number of replicates", min_value=1, step=1, value=2)
list1 = [i.strip() for i in input1.split(",") if i.strip()]
list2 = [i.strip() for i in input2.split(",") if i.strip()]

if st.button("Create class names for QuPath"):
   list_of_samples = generate_combinations(list1, list2, input3)
   json_data = {"pathClasses": []}
   for i, name in enumerate(list_of_samples):
      json_data["pathClasses"].append({
         "name": name,
         "color": java_colors[i % len(java_colors)]
      })
   with open("classes.json", "w") as f:
      json.dump(json_data, f, indent=2)

   st.download_button("Download Samples and Wells file for Qupath", 
                     data=Path('./classes.json').read_text(), 
                     file_name="classes.json")

st.image(image="./assets/sample_names_example.png",
         caption="Example of class names for QuPath")
st.divider()

###############################################
## EXTRA 2: Create default samples and wells ##
###############################################

st.markdown("""
            ## Extra 2: Create samples and wells  
            ### To designate which samples go to which wells in the collection device
            Every QuPath class represents one sample, therefore each class needs one designated well for collection.  
            Default wells are spaced (C3, C5, C7) for easier pipetting, modify at your discretion.  
            The file can be opened by any text reader Word, Notepad, etc.  
            """)

input4 = st.text_area("Enter first categorical:", placeholder="example: celltype_A, celltype_B")
input5 = st.text_area("Enter second categorical:", placeholder="example: control, drug_treated")
input6 = st.number_input("Enter number of reps", min_value=1, step=1, value=2)
list3 = [i.strip() for i in input4.split(",") if i.strip()]
list4 = [i.strip() for i in input5.split(",") if i.strip()]

if st.button("Create Samples and wells scheme with default wells"):
   spaced_list_of_acceptable_wells = create_list_of_acceptable_wells()[::2]
   list_of_samples = generate_combinations(list3, list4, input6)
   samples_and_wells = create_default_samples_and_wells(list_of_samples, spaced_list_of_acceptable_wells)
   with open("samples_and_wells.json", "w") as f:
      json.dump(samples_and_wells, f, indent=4)
   st.download_button("Download Samples and Wells file",
                     data=Path('./samples_and_wells.json').read_text(),
                     file_name="samples_and_wells.txt")
   
st.image(image="./assets/samples_and_wells_example.png",
         caption="Example of samples and wells scheme")
st.divider()

###############################################
### EXTRA 3: Color contours with categorical ##
###############################################

st.markdown("""
            ## Extra 3: Color contours with categorical  
            This tool was born to let you check which shapes have been collected based on a simple excel sheet table.
            It can also color the shapes based on any categorical values.

            Instructions: 
             - You must upload the .geojson file with classified annotations.
             - You must upload a csv with two columns:
               - Class name, this has to match the annotation classification names exactly.  
               - Categorical column name, this can be any set of categoricals, For example: collection status. max 6 categories or colors repeat.
            """)


st.write("Upload geojson file you would like to color with metadata")
geojson_file = st.file_uploader("Choose a geojson file", accept_multiple_files=False)

st.write("Upload table as csv with two columns")
metadata_file = st.file_uploader("Choose a csv file", accept_multiple_files=False)
metadata_name_key = st.text_input("Column header with shape class names", placeholder="Class name")
metadata_variable_key = st.text_input("Column header to color shapes with", placeholder="Categorical column name")

if st.button("Process metadata and geojson, for labelled shapes"):
   process_geojson_with_metadata(
      path_to_geojson=geojson_file,
      path_to_csv=metadata_file,
      metadata_name_key=metadata_name_key,
      metadata_variable_key=metadata_variable_key)
   
   st.download_button("Download lablled shapes", 
                     Path(f"./{geojson_file.name.replace("geojson", metadata_variable_key + "_labelled_shapes.geojson")}").read_text(),
                     f"./{geojson_file.name.replace("geojson", metadata_variable_key + "_labelled_shapes.geojson")}")