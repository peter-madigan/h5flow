# h5flow

A basic MPI framework to create simple sequential workflows, looping over
a dataset within a structured HDF5 file. All MPI calls are hidden behind an API
to allow for (hopefully) seamless running in either a single-process or a
multi-process environment.

## installation

To setup a fresh conda environment::

    conda create --name <env> --file environment.yml
    conda activate <env>
    pip install .

To update an existing environment::

    conda update --name <env> --file environment.yml
    conda activate <env>
    pip install .

To run tests::

    pytest

To run mpi tests::

    mpiexec pytest --with-mpi

## usage

To run a single-process workflow::

    h5flow -o <output file>.h5 -c <config file>.yaml\
        -i <input file, opt.> -s <start position, opt.> -e <end position, opt.>

To run a parallelized workflow::

    mpiexec h5flow -o <output file>.h5 -c <config file>.yaml\
        -i <input file, opt.> -s <start position, opt.> -e <end position, opt.>

Alternative entry points::

    python -m h5flow <args>
    run_h5flow.py <args>

# h5flow hdf5 structure

`h5flow` requires a specific, table-like hdf5 structure with references
between datasets. Each dataset is expected to be stored within a group path::

    /<dataset0_path>/data
    /<dataset1_path>/data
    /<dataset2_path>/data

Datasets are expected to be single-dimesional structured arrays. References
between datasets are expected to be stored alongside the parent dataset::

    /<dataset0_path>/data
    /<dataset0_path>/ref/<dataset1_path>/ref # references from dataset0 -> dataset1
    /<dataset0_path>/ref/<dataset2_path>/ref # references from dataset0 -> dataset2
    /<dataset1_path>/data
    /<dataset1_path>/ref/<dataset0_path>/ref # references from dataset1 -> dataset0
    ...

with the same dimensions as the parent dataset.

To facilitate fast + parallel read/writes there is a companion structured
dataset `ref_region` at the corresponding position as the `ref` dataset that
indicates where to look in the reference dataset for the corresponding row.
E.g.::

    /<dataset0_path>/data
    /<dataset0_path>/ref/<dataset1_path>/ref # references from dataset0 -> dataset1 (and back)
    /<dataset0_path>/ref/<dataset1_path>/ref_region # regions for dataset0 -> dataset1 reference
    /<dataset0_path>/ref/<dataset2_path>/ref # references from dataset0 -> dataset2 (and back)
    /<dataset0_path>/ref/<dataset2_path>/ref_region # regions for dataset0 -> dataset2 reference

The `.../ref_region` datasets are a 1D structured array with fields `'start': int`
and `'stop': int`. These represent the min and max indices of the `.../ref` array
that contain the corresponding index. So for example::

    data0 = np.array([0, 1, 2])
    data1 = np.array([0, 1, 2, 3])

    ref = np.array([[0,1], [1,2]]) # links data0[0] <-> data1[1], data0[1] <-> data1[2]

    ref_region0 = np.array([(0,1), (1,2), (0,0)]) # ref_region for data0, the (0,0) entries correspond to entries without references
    ref_region1 = np.array([(0,0), (0,1), (1,2), (0,0)]) # ref_region for data1

## example structure

Let's walk through an example in detail. Let's say we have two datasets `A` and
`B`::

    /A/data
    /B/data

These must be single dimensional arrays with either a simple or structured type::

    f['/A/data'].dtype # [('id', 'i8'), ('some_val', 'f4')], either a structured array
    f['/B/data'].dtype # 'f4', or a simple array

    f['/A/data'].shape # (N,), only single dimension datasets
    f['/B/data'].shape # (M,)

Now, let's say there are references between the two datasets ()::

    /A/ref/B/ref
    /A/ref/B/ref_region
    /B/ref/A/ref_region

In particular, we've created references from `A->B` so the `../ref` is stored
(by convention) at `/A/ref/B/ref`. This `../ref` dataset is 2D of shape `(L,2)`
where `L` is not necessarily equal to `N` or `M` and it contains indices into
each of the corresponding datasets. By convention, index 0 is the "parent"
dataset (`A`) and index 1 is the "child" dataset (`B`)::

    f['/A/ref/B/ref'].shape # (L,2)
    f['/A/ref/B/ref'][:,0] # indices into f['/A/data']
    f['/A/ref/B/ref'][:,1] # indices into f['/B/data']

    linked_a = f['/A/data'][:][ f['/A/ref/B/ref'][:,0] ] # data from A that can be linked to dataset B (note that you must load the dataset before the fancy indexing can be applied)
    linked_b = f['/B/data'][:][ f['/A/ref/B/ref'][:,1] ] # data from B that can be linked to dataset A
    linked_a.shape == linked_b.shape # (L,)

Converting this into a dataset that can be broadcast back into either the `A` or
`B` shape is facilitated with a helper de-referencing function::

    from h5flow.data import dereference

    b2a = dereference(
        slice(0, 1000),     # indices of A to load references for, shape: (n,)
        f['/A/ref/B/ref'],  # references to use, shape: (L,)
        f['/B/data']        # dataset to load, shape: (M,)
        )
    b2a.shape # (n,l), where l is the max number of B items associated with a row in A
    b2a.dtype == f['/B/data'].dtype # True!

    b_sum = b2a.sum(axis=-1) # use numpy masked array interface to operate on the b2a array
    b_sum.shape # (n,), data can be broadcast back onto your selected indices

And inverse relationships can be found by redefining the "ref_direction":::

    a2b = dereference(
        slice(0, 250),      # indices of B to load references for, shape: (m,)
        f['/A/ref/B/ref'],  # references to use, same as before, shape: (L,)
        f['/A/data'],       # dataset to load, shape: (N,)
        ref_direction = (1,0) # now use references from 1->0 (B->A) [default is (0,1)]
        )
    a2b.shape # (m,q), where q is the max number of A items associated with a row in B
    a2b.dtype == f['/A/data'].dtype # True!

This works just fine - until you start needing to keep track of a very large
number of references (`~50000`). In that case, we use the special
`region` (or `../ref_region` as it is called in the HDF5 file) dataset / array
to facilitate only partially loading from the reference dataset::

    b2a_subset = dereference(
        slice(0, 1000)      # indices of A to load references for, shape: (n,)
        f['/A/ref/B/ref'],  # references to use, shape: (L,)
        f['/B/data'],       # dataset to load, shape: (M,)
        region = f['/A/ref/B/ref_region'] # lookup regions in references, shape: (N,)
        )
    b2a_subset == b2a # same result as before, but internally this is handled in a much more efficient manner

    %timeit dereference(0, f['/A/ref/B/ref'], f['/B/data']) # runtime: max(100ns * len(f['/A/ref/B/ref']), 1ms)
    %timeit dereference(0, f['/A/ref/B/ref'], f['/B/data'], f['/A/ref/B/ref_region']) # runtime: ~5ms

# h5flow workflow

`h5flow` uses a yaml config file to define the workflow. The main definition of
the workflow is defined under the `flow` key::

    flow:
        source: <dataset to loop over, or generator name>
        stages: [<first sequential stage name>, <second sequential stage name>]
        drop: [<dataset name, opt.>]

The `source` defines the loop source dataset. By default, you may specify an
existing dataset and an `H5FlowDatasetLoopGenerator` will be used. `stages`
defines the names and sequential order of the analysis stages should be executed
on each data chunk provided by the generator. Optionally, `drop` defines a list
of datasets to delete from the output file after the run loop completes.

## generators

To define a generator, specify the name, an `H5FlowGenerator`-inheriting
classname, along with any desired parameters at the top level within the yaml
file::

    dummy_generator:
        classname: DummyGenerator
        dset_name: <dataset to be accessed by each stage>
        params:
            dummy_param: value

For both generators and stages, classes will be discovered for within the
current directory, the `./h5flow_modules/` directory, or the `h5flow/modules`
directory (in that order) and automatically loaded upon runtime.

## stages

To define a stage, specify the name, an `H5FlowStage`-inheriting classname, along
with any desired parameters at the top level within the yaml file::

    flow:
        source: generator_stage_or_path_to_a_dataset
        stages: [dummy_stage0, dummy_stage1]

    dummy_stage0:
        classname: DummyStage
        params:
            dummy_param0: 10
            dummy_param1: [a,list,of,strings]

    dummy_stage1:
        classname: OtherDummyStage

You can also specify specific datasets to load that is linked to the current
loop dataset with the `requires` field::

    dummy_stage_requires:
        classname: DummyStage
        requires:
            - <path to a dataset that has source <-> dset references>
            - <path to a second dataset with source <-> dset references>

This will load a ``numpy`` masked array into the ``cache`` under a key of the
same path.

You can specify complex linking paths to load data from references to references
(or references to references to references ...) by specifying a path and a
name:

    dummy_stage_complex_requires:
        classname: DummyStage
        requires:
            - name: <name to use in the cache>
              path: [<path to first dataset>, <path to second dataset>, ...]

which will load the data at ``source -> <first dataset> -> <second dataset>``.

Finally, you can also indicate if you just want to load an index into the final
dataset (rather than the data) with the ``index_only`` flag::

    dummy_stage_index_requires:
        classname: DummyStage
        requires:
            - name: <name to use in cache>
              path: [<first dataset>, <second dataset>]
              index_only: True

# writing an `H5FlowStage`

Any `H5FlowStage`-inheriting class has 4 main components:
    1. a constructor (`__init__()`)
    2. class attributes
    3. an initialization `init()` method
    4. and a `run()` method


None of the methods are required for the class to function within `h5flow`, but
each provide particular access points into the flow sequence.

First, the constructor is called when the flow sequence is first created and
is passed each of the ``<key>: <value>`` pairs declared in the config yaml. For
example, the parameters declared in the following config file::

    example:
        classname: ExampleStage
        params:
            parameter_name: parameter_value

can be accessed with a constructor::

    class ExampleStage(H5FlowStage):

        default_parameter = 0

        def __init__(self, **params):
            super(ExampleStage,self).__init__(**params) # needed to inherit H5FlowStage functionality

            parameter = params.get('parameter_name', default_parameter)

Next, class attributes (``default_parameter`` above) can be used to declare class-
specific data (e.g. default values for parameters).

Then, the ``init(self, source_name)`` method is called just before entering the
loop. Information about which dataset will be used in the loop is provided to
allow for initialization of dataset-dependent properties (or error out if the
dataset is somehow invalid for the class). Use this function to initialize new
datasets and write meta-data. See the ``h5flow_modules/examples.py`` for an
working example.

Finally, the ``run(self, source_name, source_slice, cache)`` method is called
at each step of the loop. This is where the bulk of the processing occurs.
``source_name`` is a string pointing to the current loop dataset. ``source_slice``
provides a python ``slice`` object into the full ``source_name`` data array for
the current loop iteration. ``cache`` is a python ``dict`` object filled with
pre-loaded data of the ``source_slice`` into the ``source_name`` dataset and any
``required`` datasets specified by the config yaml. Items deleted from the
``cache`` will be reloaded from the underlying hdf5 file, if required by
downstream stages. Reading and writing other data objects from the file can be
done via the ``H5FlowDataManager`` object within ``self.data_manager``. Refer to
the ``h5flow_modules/examples.py`` for a working example.

# writing an `H5FlowGenerator`

I haven't written this piece yet...
