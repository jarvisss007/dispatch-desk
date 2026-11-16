"""STAND-IN: the city table the desk uses to place a stop it only knows by town.

In the original system this table lived in a larger fleet module. Only CITIES
is used by the Dispatch Desk: (name, latitude, longitude) of the city centre,
public information, good to a few miles.
"""

CITIES = [
    # Los Angeles basin / Inland Empire
    ("Fontana, CA", 34.092, -117.435), ("Bloomington, CA", 34.070, -117.396),
    ("Ontario, CA", 34.063, -117.651), ("Rancho Cucamonga, CA", 34.106, -117.593),
    ("Riverside, CA", 33.953, -117.396), ("Moreno Valley, CA", 33.943, -117.230),
    ("San Bernardino, CA", 34.108, -117.290), ("Rialto, CA", 34.106, -117.370),
    ("Colton, CA", 34.074, -117.314), ("Redlands, CA", 34.056, -117.183),
    ("Corona, CA", 33.875, -117.566), ("Chino, CA", 34.012, -117.689),
    ("Pomona, CA", 34.055, -117.750), ("Perris, CA", 33.783, -117.229),
    ("Carson, CA", 33.832, -118.282), ("Long Beach, CA", 33.770, -118.194),
    ("Bell, CA", 33.978, -118.187), ("Commerce, CA", 34.001, -118.160),
    ("Los Angeles, CA", 34.052, -118.244), ("Anaheim, CA", 33.836, -117.914),
    ("San Diego, CA", 32.716, -117.161), ("Barstow, CA", 34.896, -117.017),
    # Central Valley
    ("Bakersfield, CA", 35.373, -119.019), ("Delano, CA", 35.769, -119.247),
    ("Tulare, CA", 36.208, -119.347), ("Visalia, CA", 36.330, -119.292),
    ("Exeter, CA", 36.296, -119.142), ("Porterville, CA", 36.065, -119.017),
    ("Fresno, CA", 36.738, -119.787), ("Sanger, CA", 36.708, -119.556),
    ("Madera, CA", 36.961, -120.061), ("Modesto, CA", 37.639, -120.997),
    ("Stockton, CA", 37.958, -121.291), ("Lathrop, CA", 37.823, -121.277),
    ("Tracy, CA", 37.740, -121.426),
    # Arizona
    ("Phoenix, AZ", 33.448, -112.074), ("Tolleson, AZ", 33.450, -112.259),
    ("Goodyear, AZ", 33.435, -112.358), ("Buckeye, AZ", 33.370, -112.584),
    ("Glendale, AZ", 33.539, -112.186), ("Peoria, AZ", 33.581, -112.237),
    ("Waddell, AZ", 33.573, -112.431), ("Scottsdale, AZ", 33.494, -111.926),
    ("Tempe, AZ", 33.426, -111.940), ("Mesa, AZ", 33.415, -111.831),
    ("Chandler, AZ", 33.306, -111.841), ("Tucson, AZ", 32.222, -110.975),
    ("Yuma, AZ", 32.693, -114.628),
    # Nevada
    ("Las Vegas, NV", 36.170, -115.140), ("North Las Vegas, NV", 36.199, -115.118),
    ("Henderson, NV", 36.040, -114.982), ("Primm, NV", 35.610, -115.388),
    ("Reno, NV", 39.530, -119.814),
]
