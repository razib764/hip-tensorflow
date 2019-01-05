from os import listdir
from os.path import isfile, join
import pandas as pd
import sys
import time

from hip.models import TensorHIP
from hip.utils import load_data_from_csv, print_params_to_tsv

if __name__ == '__main__':
    sys.stderr.write("loading the files\n")
    sys.stderr.flush()

    if len(sys.argv) > 1:
        input_path = sys.argv[1]
        feature_index = int(sys.argv[2])
    else:
        input_path = 'data/sample_data_2/'
        feature_index = 0 

    input_files = []
    xs = []
    ys = []
    file_paths = []
    for f in listdir(input_path):
        file_path = join(input_path, f)
        if isfile(file_path) and file_path.lower().endswith('.csv'):
            input_files.append(file_path)
            features, target, feature_names, target_name = load_data_from_csv(file_path)

            xs.append([features[feature_index]])
            ys.append(target)
            file_paths.append(file_path)

    sys.stderr.write("beginning the training\n")
    sys.stderr.flush()

    start_time = time.time()

    hip_model = TensorHIP(xs=xs,
                  ys=ys,    
                  feature_names=feature_names,
                  num_initializations=1,
                  verbose=False)
    hip_model.train()    

    sys.stderr.write("training completed in {} seconds\n".format(time.time() - start_time))

    model_params = hip_model.get_model_parameters()
    print_params_to_tsv(params=model_params, feature_name=feature_names[feature_index])