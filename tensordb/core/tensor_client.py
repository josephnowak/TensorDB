import loguru
import xarray
import os
import json
import fsspec

from typing import Dict, List, Any, Union, Tuple
from numpy import nan, array
from pandas import Timestamp
from loguru import logger
from dask.delayed import Delayed

from tensordb.core.cached_tensor import CachedTensorHandler
from tensordb.file_handlers import (
    ZarrStorage,
    BaseStorage,
    JsonStorage
)
from tensordb.core.utils import internal_actions
from tensordb.config.handlers import MAPPING_STORAGES


class TensorClient:
    """

    It's client designed to handle tensor data in a simpler way and it's built with Xarray,
    it can support the same files than Xarray but those formats need to be implement
    using the `BaseStorage` interface proposed in this package.

    As we can create Tensors with multiple Storage that needs differents settings or parameters we must create
    a "Tensor Definition" which is basically a dictionary that specify the behaviour that you want to have
    every time you call a method of one Storage. The definitions are very simple to create (there are few internal keys)
    , you only need to use as key the name of your method and as value a dictionary containing all the necessary
    parameters to use it, you can see some examples in the ``Examples`` section

    Additional features:
        1. Support for any backup system using fsspec package and a specific method to simplify the work (backup).
        2. Creation or modification of new tensors using dynamic string formulas (even string python code).
        3. The read method return a lazy Xarray DataArray instead of only retrieve the data.
        4. It's easy to inherit the class and add customized methods.
        5. The backups can be faster and saver because you can modify them as you want, an example of this is the
           ZarrStorage which has a checksum of every chunk of every tensor stored to
           avoid uploading or downloading unnecessary data and is useful to check the integrity of the data.

    Parameters
    ----------
    local_base_map: fsspec.FSMap
       FSMap instaciated with the local path that you want to use to store all tensors.

    backup_base_map: fsspec.FSMap
        FSMap instaciated with the backup path that you want to use to store all tensors.

    synchronizer: str
        Some of the Storages used to handle the files support a synchronizer, this parameter is used as a default
        synchronizer option for everyone of them (you can pass different synchronizer to every tensor).

    **kwargs: Dict
        Useful when you want to inherent from this class.

    Examples
    --------

    Store and read a simple tensor:

        >>> from tensordb import TensorClient
        >>> import xarray
        >>> import fsspec
        >>>
        >>> tensor_client = TensorClient(
        ...     local_base_map=fsspec.get_mapper('test_db'),
        ...     backup_base_map=fsspec.get_mapper('test_db' + '/backup'),
        ...     synchronizer='thread'
        ... )
        >>>
        >>> # Adding an empty tensor definition (there is no personalization)
        >>> tensor_client.add_tensor_definition(
        ...     tensor_id='dummy_tensor_definition',
        ...     new_data={
        ...         # This key is used for modify options of the Storage constructor
        ...         # (documented on the reserved keys section of this method)
        ...         'handler': {
        ...             # modify the default Storage for the zarr_storage
        ...             'data_handler': 'zarr_storage'
        ...         }
        ...     }
        ... )
        >>>
        >>> # create a new empty tensor, you must always call this method to start using the tensor.
        >>> tensor_client.create_tensor(path='tensor1', tensor_definition='dummy_tensor_definition')
        >>>
        >>> new_data = xarray.DataArray(
        ...     0.0,
        ...     coords={'index': list(range(3)), 'columns': list(range(3))},
        ...     dims=['index', 'columns']
        ... )
        >>>
        >>> # Storing tensor1 on disk
        >>> tensor_client.store(path='tensor1', new_data=new_data)
        <xarray.backends.zarr.ZarrStore object at 0x000001FFBE9ADB80>
        >>>
        >>> # Reading the tensor1 (normally you will get a lazy Xarray (use dask in the backend))
        >>> tensor_client.read(path='tensor1')
        <xarray.DataArray 'data' (index: 3, columns: 3)>
        array([[0., 0., 0.],
               [0., 0., 0.],
               [0., 0., 0.]])
        Coordinates:
          * columns  (columns) int32 0 1 2
          * index    (index) int32 0 1 2

    Storing a tensor from a string formula (if you want to create an 'on the fly' tensor using formula see
    the docs :meth:`TensorClient.read_from_formula`:

        >>> # Creating a new tensor definition using a formula that depend on the previous stored tensor
        >>> tensor_client.add_tensor_definition(
        ...     tensor_id='tensor_formula',
        ...     new_data={
        ...         'store': {
        ...             # read the docs of this method to understand the behaviour of the data_methods key
        ...             'data_methods': ['read_from_formula'],
        ...         },
        ...         'read_from_formula': {
        ...             'formula': '`tensor1` + 1 + `tensor1` * 10'
        ...         }
        ...     }
        ... )
        >>>
        >>> # create a new empty tensor, you must always call this method to start using the tensor.
        >>> tensor_client.create_tensor(path='tensor_formula', tensor_definition='tensor_formula')
        >>>
        >>> # Storing tensor_formula on disk, check that now we do not need to send the new_data parameter, because it is generated
        >>> # from the formula that we create previously
        >>> tensor_client.store(path='tensor_formula')
        <xarray.backends.zarr.ZarrStore object at 0x000001FFBEA93C40>
        >>>
        >>> # Reading the tensor_formula (normally you will get a lazy Xarray (use dask in the backend))
        >>> tensor_client.read(path='tensor_formula')
        <xarray.DataArray 'data' (index: 3, columns: 3)>
        array([[1., 1., 1.],
               [1., 1., 1.],
               [1., 1., 1.]])
        Coordinates:
          * columns  (columns) int32 0 1 2
          * index    (index) int32 0 1 2

    Appending a new row and a new column to a tensor:

        >>> # Appending a new row and a new columns to the tensor_formula stored previously
        >>> new_data = xarray.DataArray(
        ...     2.,
        ...     coords={'index': [3], 'columns': list(range(4))},
        ...     dims=['index', 'columns']
        ... )
        >>>
        >>> # Appending the data, you can use the compute=False parameter if you dont want to execute this immediately
        >>> tensor_client.append('tensor_formula', new_data=new_data)
        [<xarray.backends.zarr.ZarrStore object at 0x000001FFBEB77AC0>, <xarray.backends.zarr.ZarrStore object at 0x000001FFBEB779A0>]
        >>>
        >>> # Reading the tensor_formula (normally you will get a lazy Xarray (use dask in the backend))
        >>> tensor_client.read('tensor_formula')
        <xarray.DataArray 'data' (index: 4, columns: 4)>
        array([[ 1.,  1.,  1., nan],
               [ 1.,  1.,  1., nan],
               [ 1.,  1.,  1., nan],
               [ 2.,  2.,  2.,  2.]])
        Coordinates:
          * columns  (columns) int32 0 1 2 3
          * index    (index) int32 0 1 2 3

    TODO:
        1. Add more examples to the documentation

    """

    def __init__(self,
                 local_base_map: fsspec.FSMap,
                 backup_base_map: fsspec.FSMap,
                 max_files_on_disk: int = 0,
                 synchronizer: str = None,
                 **kwargs):

        self.local_base_map = local_base_map
        self.backup_base_map = backup_base_map
        self.open_base_store: Dict[str, Dict[str, Any]] = {}
        self.max_files_on_disk = max_files_on_disk
        self.synchronizer = synchronizer
        self._tensors_definition = JsonStorage(
            path='tensors_definition',
            local_base_map=self.local_base_map,
            backup_base_map=self.backup_base_map,
        )

    def add_tensor_definition(self, tensor_id: str, new_data: Dict) -> Dict:
        """
        Add (store) a new tensor definition (internally is stored as a JSON file).

        Reserved Keywords:
            handler: This key is used to personalize the Storage used for the tensor, inside it you can use the next
            reserved keywords:

                1.  data_handler: Here you put the name of your storage (default zarr_storage), you can see
                all the names in the variable MAPPING_STORAGES.

            You can personalize the way that any Storage method is used specifying it in the tensor_definition,
            this is basically add a key with the name of the method and inside of it you can add any kind of parameters,
            but there are some reserved words that are used by the Tensorclient to add specific functionalities,
            these are described here:

                1. data_methods:
                    The data methods are basically Storage methods (they are called following the same logic
                    of storage_method_caller) that must be called before the execution of your "method_name",
                    so it is really useful if you need to read the data from an specific place or make some
                    transformation before apply your method. You need to pass a list with the names of your methods
                    or if you want a personalization beyond the tensor_definition you can pass a list of tuples where
                    the first element of every tuple is the name of the method and the second is Dict with parameters.

                2. customized_method:
                    Modify the method called, this is useful if you want to overwrite the defaults
                    methods of storage, read, etc for some specific tensors, this is normally used when you want to read
                    a tensor on the fly with a formula.


        Parameters
        ----------
        tensor_id: str
            name used to identify the tensor definition.

        new_data: Dict
            Description of the definition, find an example of the format here.

        Examples
        --------
        Add examples.

        See Also:
            Read the :meth:`TensorClient.storage_method_caller` to learn how to personalize your methods

        """
        self._tensors_definition.store(name=tensor_id, new_data=new_data)

    def create_tensor(self, path: str, tensor_definition: Union[str, Dict], **kwargs):
        """
        Create the path and the first file of the tensor which store the necessary metadata to use it,
        this method must always be called before start to write in the tensor.

        Parameters
        ----------
        path: str
            Indicate the location where your tensor is going to be allocated.

        tensor_definition: str, Dict
            This can be an string which allow to read a previously created tensor definition or a completly new
            tensor_definition in case that you pass a Dict.

        **kwargs: Dict
            Aditional metadata for the tensor.

        See Also:
            If you want to personalize any method of your Storage read the `TensorClient.storage_method_caller` doc


        """
        json_storage = JsonStorage(path, self.local_base_map, self.backup_base_map)
        kwargs.update({'definition': tensor_definition})
        json_storage.store(new_data=kwargs, name='tensor_definition.json')

    def get_tensor_definition(self, name: str) -> Dict:
        """
        Retrieve a created tensor definition.

        Parameters
        ----------
        name: str
            name of the tensor definition.

        Returns
        -------
        A dict containing all the information of the tensor definition previusly stored.

        """
        return self._tensors_definition.read(name)

    def get_storage_tensor_definition(self, path) -> Dict:
        """
        Retrieve the tensor definition of an specific tensor.

        Parameters
        ----------
        path: str
            Location of your stored tensor.

        Returns
        -------
        A dict containing all the information of the tensor definition previusly stored.

        """
        json_storage = JsonStorage(path, self.local_base_map, self.backup_base_map)
        if not json_storage.exist('tensor_definition.json'):
            raise KeyError('You can not use a tensor without first call the create_tensor method')
        tensor_definition = json_storage.read('tensor_definition.json')['definition']
        if isinstance(tensor_definition, dict):
            return tensor_definition
        return self.get_tensor_definition(tensor_definition)

    def _get_handler(self, path: str, tensor_definition: Dict = None) -> BaseStorage:
        handler_settings = self.get_storage_tensor_definition(path) if tensor_definition is None else tensor_definition
        handler_settings = handler_settings.get('handler', {})
        handler_settings['synchronizer'] = handler_settings.get('synchronizer', self.synchronizer)

        data_handler = ZarrStorage
        if 'data_handler' in handler_settings:
            data_handler = MAPPING_STORAGES[handler_settings['data_handler']]

        data_handler = data_handler(
            local_base_map=self.local_base_map,
            backup_base_map=self.backup_base_map,
            path=path,
            **handler_settings
        )
        if path not in self.open_base_store:
            self.open_base_store[path] = {
                'first_read_date': Timestamp.now(),
                'num_use': 0
            }
        self.open_base_store[path]['data_handler'] = data_handler
        self.open_base_store[path]['num_use'] += 1
        return self.open_base_store[path]['data_handler']

    def _customize_handler_action(self, path: str, action_type: str, **kwargs):
        tensor_definition = self.get_storage_tensor_definition(path)
        kwargs.update({
            'action_type': action_type,
            'handler': self._get_handler(path=path, tensor_definition=tensor_definition),
            'tensor_definition': tensor_definition
        })

        method_settings = tensor_definition.get(kwargs['action_type'], {})
        if 'customized_method' in method_settings:
            method = method_settings['customized_method']
            if method in internal_actions:
                return getattr(self, method)(path=path, **kwargs)
            return getattr(self, method)(**kwargs)

        if 'data_methods' in method_settings:
            kwargs['new_data'] = self._apply_data_methods(data_methods=method_settings['data_methods'], **kwargs)

        return getattr(kwargs['handler'], action_type)(**{**kwargs, **method_settings})

    def _apply_data_methods(self,
                            data_methods: List[Union[str, Tuple[str, Dict]]],
                            tensor_definition: Dict,
                            **kwargs):
        results = {**{'new_data': None}, **kwargs}
        for method in data_methods:
            if isinstance(method, (list, tuple)):
                method_name, parameters = method[0], method[1]
            else:
                method_name, parameters = method, tensor_definition.get(method, {})
            result = getattr(self, method_name)(
                **{**parameters, **results},
                tensor_definition=tensor_definition
            )
            if method_name in internal_actions:
                continue

            results.update(result if isinstance(result, dict) else {'new_data': result})

        return results['new_data']

    def storage_method_caller(self, path: str, method_name: str, **kwargs) -> Any:
        """
        Calls an specific method of a Storage, this include send the parameters specified in the tensor_definition
        or modifying the behaviour of the method based in your tensor_definition
        (read :meth:`TensorClient.add_tensor_definition` for more info of how to personalize your method).

        If you want to know the specific behaviour of the method that you are using,
        please read the specific documentation of the Storage that you are using or read `BaseStorage`.

        Parameters
        ----------
        path: str
            Location of your stored tensor.

        method_name: str
            Name of the method used by the Storage.

        **kwargs: Dict
            Extra parameters that are going to be used by the Storage, in case that any of this parameter
            match with the ones provided in the tensor_definition they will overwrite them.

        Returns
        -------
        The result vary depending on the method called.

        """
        return self._customize_handler_action(path=path, **{**kwargs, **{'action_type': method_name}})

    def read(self, path: str, **kwargs) -> xarray.DataArray:
        """
        Calls :meth:`TensorClient.storage_method_caller` with read as method_name (has the same parameters).

        Returns
        -------
        An xarray.DataArray that allow to read the data in the path.

        """
        return self.storage_method_caller(path=path, method_name='read', **kwargs)

    def append(self, path: str, **kwargs) -> List[xarray.backends.common.AbstractWritableDataStore]:
        """
        Calls :meth:`TensorClient.storage_method_caller` with append as method_name (has the same parameters).

        Returns
        -------
        Returns a List of xarray.backends.common.AbstractWritableDataStore objects,
        which is used as an interface for the corresponding backend that you select in xarray (the Storage).

        """
        return self.storage_method_caller(path=path, method_name='append', **kwargs)

    def update(self, path: str, **kwargs) -> xarray.backends.common.AbstractWritableDataStore:
        """
        Calls :meth:`TensorClient.storage_method_caller` with update as method_name (has the same parameters).

        Returns
        -------
        Returns a single the xarray.backends.common.AbstractWritableDataStore object,
        which is used as an interface for the corresponding backend that you select in xarray (the Storage).

        """
        return self.storage_method_caller(path=path, method_name='update', **kwargs)

    def store(self, path: str, **kwargs) -> xarray.backends.common.AbstractWritableDataStore:
        """
        Calls :meth:`TensorClient.storage_method_caller` with store as method_name (has the same parameters).

        Returns
        -------
        Returns a single the xarray.backends.common.AbstractWritableDataStore object,
        which is used as an interface for the corresponding backend that you select in xarray (the Storage).

        """
        return self.storage_method_caller(path=path, method_name='store', **kwargs)

    def upsert(self, path: str, **kwargs) -> List[xarray.backends.common.AbstractWritableDataStore]:
        """
        Calls :meth:`TensorClient.storage_method_caller` with upsert as method_name (has the same parameters).

        Returns
        -------
        Returns a List of xarray.backends.common.AbstractWritableDataStore objects,
        which is used as an interface for the corresponding backend that you select in xarray (the Storage).

        """
        return self.storage_method_caller(path=path, method_name='upsert', **kwargs)

    def backup(self, path: str, **kwargs) -> xarray.DataArray:
        """
        Calls :meth:`TensorClient.storage_method_caller` with backup as method_name (has the same parameters).

        Returns
        -------
        Depends of every Storage.

        """
        return self.storage_method_caller(path=path, method_name='backup', **kwargs)

    def update_from_backup(self, path: str, **kwargs) -> Any:
        """
        Calls :meth:`TensorClient.storage_method_caller` with update_from_backup as
        method_name (has the same parameters).

        Returns
        -------
        Depends of every Storage.

        """
        return self.storage_method_caller(path=path, method_name='update_from_backup', **kwargs)

    def set_attrs(self, path: str, **kwargs):
        """
        Calls :meth:`TensorClient.storage_method_caller` with set_attrs as
        method_name (has the same parameters).

        """
        return self.storage_method_caller(path=path, method_name='set_attrs', **kwargs)

    def get_attrs(self, path: str, **kwargs) -> Dict:
        """
        Calls :meth:`TensorClient.storage_method_caller` with get_attrs as method_name
        (has the same parameters).

        Returns
        -------
        A dict with the attributes of the tensor (metadata).
        """
        return self.storage_method_caller(path=path, method_name='get_attrs', **kwargs)

    def close(self, path: str, **kwargs) -> Any:
        """
        Calls :meth:`TensorClient.storage_method_caller` with close as method_name (has the same parameters).

        """
        return self.storage_method_caller(path=path, method_name='close', **kwargs)

    def delete_file(self, path: str, **kwargs) -> Any:
        """
        Calls :meth:`TensorClient.storage_method_caller` with delete_file as method_name (has the same parameters).
        """
        return self.storage_method_caller(path=path, method_name='delete_file', **kwargs)

    def exist(self, path: str, **kwargs) -> bool:
        """
        Calls :meth:`TensorClient.storage_method_caller` with exist as method_name (has the same parameters).

        Returns
        -------
        A bool indicating if the file exist or not (True means yes).
        """
        # TODO: this method fail if the tensor was not created, so this must be fixed it should return False
        return self._get_handler(path).exist(**kwargs)

    def exist_tensor_definition(self, path: str):
        """
        Check if exist an specific definition.

        Parameters
        ----------
        path: str
            Location of your stored tensor.

        Returns
        -------
        A bool indicating if the definition exist or not (True means yes).

        """
        base_storage = BaseStorage(path, self.local_base_map, self.backup_base_map)
        return 'tensor_definition.json' in base_storage.backup_map

    def get_cached_tensor_manager(self, path, max_cached_in_dim: int, dim: str, **kwargs):
        """
        Create a `CachedTensorHandler` object which is used for multiples writes of the same file.

        Parameters
        ----------
        path: str
            Location of your stored tensor.

        max_cached_in_dim: int
            `CachedTensorHandler.max_cached_in_dim`

        dim: str
            `CachedTensorHandler.dim`

        **kwargs
            Parameters used for the internal Storage that you choosed.

        Returns
        -------
        A `CachedTensorHandler` object.

        """
        handler = self._get_handler(path, **kwargs)
        return CachedTensorHandler(
            file_handler=handler,
            max_cached_in_dim=max_cached_in_dim,
            dim=dim
        )

    def read_from_formula(self,
                          tensor_definition: Dict = None,
                          new_data: xarray.DataArray = None,
                          formula: str = None,
                          use_exec: bool = False,
                          **kwargs):
        """
        This is one of the most important methods of the `TensorClient` class, basically it allows to define
        formulas that use the tensors stored with a simple strings, so you can create new tensors from this formulas
        (make use of python eval and the same syntax that you use with Xarray).
        This is very flexible, you can even create relations between tensor and the only extra thing
        you need to know is that you have to wrap the path of your tensor with "`" to be parsed and
        read automatically.

        Another important chracteristic is that you can even pass entiere python codes to create this new tensors
        (it make use of python exec so use use_exec parameter as True).

        Parameters
        ----------
        tensor_definition: Dict, optional
            Definition of your tensor.

        new_data: xarray.DataArray, optional
            Sometimes you can use this method in combination with others so you can pass the data that you are
            creating using this parameters (is more for internal use).

        use_exec: bool = False
            Indicate if you want to use python exec or eval for the formula.

        **kwargs
            Extra parameters used principally for when you want to use the exec option and want to add some settings
            or values.

        Examples
        --------

        Reading a tensor directly from a formula, all this is lazy evaluated:
            >>> # Creating a new tensor definition using an 'on the fly' formula
            >>> tensor_client.add_tensor_definition(
            ...     tensor_id='tensor_formula_on_the_fly',
            ...     new_data={
            ...         'read': {
            ...             # Read the section reserved Keywords
            ...             'customized_method': 'read_from_formula',
            ...         },
            ...         'read_from_formula': {
            ...             'formula': '`tensor1` + 1 + `tensor1` * 10'
            ...         }
            ...     }
            ... )
            >>>
            >>> # create a new empty tensor, you must always call this method to start using the tensor.
            >>> tensor_client.create_tensor(path='tensor_formula_on_the_fly', tensor_definition='tensor_formula_on_the_fly')
            >>>
            >>> # Now we don't need to call the store method when we want to read our tensor
            >>> # the good part is that everything is still lazy
            >>> tensor_client.read(path='tensor_formula_on_the_fly')
            <xarray.DataArray 'data' (index: 3, columns: 3)>
            array([[1., 1., 1.],
                   [1., 1., 1.],
                   [1., 1., 1.]])
            Coordinates:
              * columns  (columns) int32 0 1 2
              * index    (index) int32 0 1 2

        You can see an example of how to store a tensor from a formula in the examples of the
        constructor section in `TensorClient`

        Returns
        -------
        An xarray.DataArray object created from the formula.

        """
        if formula is None:
            formula = tensor_definition['read_from_formula']['formula']
            use_exec = tensor_definition['read_from_formula'].get('use_exec', False)

        data_fields = {}
        data_fields_intervals = array([i for i, c in enumerate(formula) if c == '`'])
        for i in range(0, len(data_fields_intervals), 2):
            name_data_field = formula[data_fields_intervals[i] + 1: data_fields_intervals[i + 1]]
            data_fields[name_data_field] = self.read(name_data_field)
        for name, dataset in data_fields.items():
            formula = formula.replace(f"`{name}`", f"data_fields['{name}']")
        if use_exec:
            d = {'data_fields': data_fields, 'new_data': new_data}
            d.update(kwargs)
            exec(formula, d)
            return d['new_data']
        return eval(formula)

    def reindex(self,
                new_data: xarray.DataArray,
                reindex_path: str,
                coords_to_reindex: List[str],
                action_type: str,
                handler: BaseStorage,
                method_fill_value: str = None,
                **kwargs) -> Union[xarray.DataArray, None]:
        if new_data is None:
            return None

        data_reindex = self.read(path=reindex_path, **kwargs)
        if action_type != 'store':
            data = handler.read()
            coords_to_reindex = {
                coord: data_reindex.coords[coord][data_reindex.coords[coord] >= data.coords[coord][-1].values]
                for coord in coords_to_reindex
            }
        else:
            coords_to_reindex = {coord: data_reindex.coords[coord] for coord in coords_to_reindex}
        return new_data.reindex(coords_to_reindex, method=method_fill_value)

    def last_valid_dim(self,
                       new_data: xarray.DataArray,
                       dim: str,
                       **kwargs) -> Union[xarray.DataArray, None]:
        if new_data is None:
            return None
        if new_data.dtype == 'bool':
            return new_data.cumsum(dim=dim).idxmax(dim=dim)
        return new_data.notnull().cumsum(dim=dim).idxmax(dim=dim)

    def replace_values(self,
                       new_data: xarray.DataArray,
                       replace_path: str,
                       value: Any = nan,
                       **kwargs) -> Union[xarray.DataArray, None]:
        if new_data is None:
            return new_data
        replace_data_array = self.read(path=replace_path, **kwargs)
        return new_data.where(replace_data_array.sel(new_data.coords), value)

    def fillna(self,
               new_data: xarray.DataArray,
               value: Any = nan,
               **kwargs) -> Union[xarray.DataArray, None]:

        if new_data is None:
            return new_data
        return new_data.fillna(value)

    def ffill(self,
              handler: BaseStorage,
              new_data: xarray.DataArray,
              dim: str,
              action_type: str,
              limit: int = None,
              **kwargs) -> Union[xarray.DataArray, None]:

        if new_data is None:
            return new_data
        data_concat = new_data
        if action_type != 'store':
            data = handler.read()
            data = data.sel({dim: data.coords[dim] < new_data.coords[dim][0]})
            if data.sizes[dim] > 0:
                data_concat = xarray.concat([data.isel({dim: [-1]}), new_data], dim=dim)

        return data_concat.ffill(dim=dim, limit=limit).sel(new_data.coords)

    def replace_last_valid_dim(self,
                               new_data: xarray.DataArray,
                               replace_path: str,
                               dim: str,
                               value: Any = nan,
                               calculate_last_valid: bool = True,
                               **kwargs) -> Union[xarray.DataArray, None]:
        if new_data is None:
            return new_data

        last_valid = self.read(path=replace_path, **kwargs)
        if calculate_last_valid:
            last_valid = self.last_valid_dim(new_data, dim)
        last_valid = new_data.coords[dim] <= last_valid.fillna(new_data.coords[dim][-1])
        return new_data.where(last_valid.sel(new_data.coords), value)
