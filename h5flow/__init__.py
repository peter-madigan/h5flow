import yaml
#!/usr/bin/env python
import argparse
import sys
from yaml import Loader
from mpi4py import MPI

from .core import H5FlowManager

def run(config, output_filename, input_filename=None, start_position=None, end_position=None):
    rank = MPI.COMM_WORLD.Get_rank()

    if rank == 0:
        print('\n~~~ CONFIG DUMP ~~~')
        with open(config,'r') as f:
            for line in f.readlines():
                print(line, end='')
    with open(config,'r') as f:
        config = yaml.load(f, Loader=Loader)

    manager = H5FlowManager(input_filename, output_filename, config, start_position=start_position, end_position=end_position)

    print(f'\n~~~ INIT {rank} ~~~')
    manager.init()

    print(f'\n~~~ RUN {rank} ~~~')
    manager.run()

    print(f'\n~~~ FINISH {rank} ~~~')
    manager.finish()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input_filename','-i', type=str, default=None, required=False, help='''input hdf5 file to loop over, optional if using a custom file generator''')
    parser.add_argument('--output_filename','-o', type=str, required=True)
    parser.add_argument('--config','-c', type=str, required=True, help='''yaml config file''')
    parser.add_argument('--start_position','-s', type=int, default=None, help='''start position within source dset (for partial file processing)''')
    parser.add_argument('--end_position','-e', type=int, default=None, help='''end position within source dset (for partial file processing)''')
    args = parser.parse_args()
    run(**vars(args))
