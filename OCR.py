import easyocr
import cv2
from matplotlib import pyplot as plt
import numpy as np

IMG_Path = '/Users/tanay/Downloads/machine-learning-AI-2.png'
reader = easyocr.Reader(['en'], gpu=False)  # this needs to run only once to load the model into memory
results = reader.readtext(IMG_Path)
results