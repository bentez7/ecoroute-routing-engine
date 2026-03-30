import json

from nrel.routee.compass import CompassApp

app = CompassApp.from_config_file("kuala_lumpur/osm_default_energy.toml")
query = [
    {
        "origin_x": 101.71168852073818, 
        "origin_y": 3.1579729495980247,
        "destination_x": 101.68994894821265,
        "destination_y": 3.156997893463524, 
        "model_name": "2016_TOYOTA_Camry_4cyl_2WD",
        "vehicle_rates": {
            "trip_distance": {"type": "distance", "factor": 0.655, "unit": "miles"},
            "trip_time": {"type": "time", "factor": 20.0, "unit": "hours"},
            "trip_energy_liquid": {"type": "energy", "factor": 3.0, "unit": "gge"},
        },
        "grid_search": {
            "test_cases": [
                {
                    "name": "least_time",
                    "weights": {
                        "trip_distance": 0,
                        "trip_time": 1,
                        "trip_energy_liquid": 0,
                    },
                },
                {
                    "name": "least_energy",
                    "weights": {
                        "trip_distance": 0,
                        "trip_time": 0,
                        "trip_energy_liquid": 1,
                    },
                },
                {
                    "name": "least_cost",
                    "weights": {
                        "trip_distance": 1,
                        "trip_time": 1,
                        "trip_energy_liquid": 1,
                    },
                },
            ]
        },
    },
]

results = app.run(query)

print(results)

def pretty_print(dict):
    print(json.dumps(dict, indent=4))

results_map = {r["request"]["name"]: r for r in results}
shortest_time_result = results_map["least_time"]
least_energy_result = results_map["least_energy"]
least_cost_result = results_map["least_cost"]

pretty_print(shortest_time_result)
# print(shortest_time_result)