"""
apipkg: control the exported namespace of a Python package.

see https://pypi.python.org/pypi/apipkg

(c) holger krekel, 2009 - MIT license
"""
import os
import sys
from types import ModuleType

# from .version import version as __version__
__version__ = '1.5'


def _py_abspath(path):
    """
    special version of abspath
    that will leave paths from jython jars alone
    """
    if path.startswith('__pyclasspath__'):

        return path
    else:
        return os.path.abspath(path)


def distribution_version(name):
    """try to get the version of the named distribution,
    returs None on failure"""
    from pkg_resources import get_distribution, DistributionNotFound
    try:
        dist = get_distribution(name)
    except DistributionNotFound:
        pass
    else:
        return dist.version


def initpkg(pkgname, exportdefs, attr=None, eager=False):
    """ initialize given package from the export definitions. """
    attr = attr or {}
    oldmod = sys.modules.get(pkgname)
    d = {}
    for name in ('__file__', '__cached__'):
        try:
            f = getattr(oldmod, name)
        except AttributeError:
            pass
        else:
            if f:
                f = _py_abspath(f)
            d[name] = f
    for name in ('__version__', '__loader__', '__package__', '__spec__'):
        try:
            d[name] = getattr(oldmod, name)
        except AttributeError:
            pass
    if hasattr(oldmod, '__path__'):
        if oldmod.__path__:
            d['__path__'] = [_py_abspath(p) for p in oldmod.__path__]
        else:
            d['__path__'] = oldmod.__path__
    if '__doc__' not in exportdefs and getattr(oldmod, '__doc__', None):
        d['__doc__'] = oldmod.__doc__
    d.update(attr)
    if hasattr(oldmod, '__dict__'):
        oldmod.__dict__.update(d)
    mod = ApiModule(pkgname, exportdefs, implprefix=pkgname, attr=d)
    sys.modules[pkgname] = mod
    # eagerload in bypthon to avoid their monkeypatching breaking packages
    if 'bpython' in sys.modules or eager:
        for module in list(sys.modules.values()):
            if isinstance(module, ApiModule):
                module.__dict__


def importobj(modpath, attrname):
    """imports a module, then resolves the attrname on it"""
    module = __import__(modpath, None, None, ['__doc__'])
    if not attrname:
        return module

    retval = module
    names = attrname.split(".")
    for x in names:
        retval = getattr(retval, x)
    return retval


class ApiModule(ModuleType):
    """the magical lazy-loading module standing"""
    def __docget(self):
        try:
            return self.__doc
        except AttributeError:
            if '__doc__' in self.__map__:
                return self.__makeattr('__doc__')

    def __docset(self, value):
        self.__doc = value
    __doc__ = property(__docget, __docset)

    def __init__(self, name, importspec, implprefix=None, attr=None):
        self.__name__ = name
        self.__all__ = [x for x in importspec if x != '__onfirstaccess__']
        self.__map__ = {}
        self.__implprefix__ = implprefix or name
        if attr:
            for name, val in attr.items():
                setattr(self, name, val)
        has_file_attr = attr and '__file__' in attr
        has_package_attr = attr and '__package__' in attr
        has_path_attr = attr and '__path__' in attr
        attribute_modpaths = set()
        for name, importspec in importspec.items():
            if isinstance(importspec, dict):
                # This module has submodules and is therefore a package
                if not has_package_attr:
                    setattr(self, '__package__', self.__name__)
                    has_package_attr = True
                if not has_path_attr:
                    setattr(self, '__path__', [])
                    has_path_attr = True
                subname = '%s.%s' % (self.__name__, name)
                apimod = ApiModule(subname, importspec, implprefix)
                sys.modules[subname] = apimod
                setattr(self, name, apimod)
            else:
                parts = importspec.split(':')
                modpath = parts.pop(0)
                attrname = parts and parts[0] or ""
                if modpath[0] == '.':
                    modpath = implprefix + modpath

                if not attrname:
                    subname = '%s.%s' % (self.__name__, name)
                    apimod = AliasModule(subname, modpath)
                    sys.modules[subname] = apimod
                    if '.' not in name:
                        setattr(self, name, apimod)
                else:
                    self.__map__[name] = (modpath, attrname)
                    if name != '__onfirstaccess__':
                        attribute_modpaths.add(modpath)
        if not has_file_attr:
            # Ensure __file__ attribute is set.
            if len(attribute_modpaths) == 1:
                # Special case: all attributes come from same module so it is
                # accurate to use __file__ from that module for this one
                self.__map__['__file__'] = (attribute_modpaths.pop(), '__file__')
            else:
                # Use a value linecache (and traceback) will handle nicely
                self.__file__ = '<apipkg-api-module>'
        if not has_package_attr:
            # Ensure __package__ is set. If we have reached this point then
            # this is not a package and __package__ should be set to the parent
            # In Python 2 __package__ is None for a top-level module, but in
            # Python 3 it is ''
            self.__package__ = self.__name__.rpartition('.')[0] or sys.__package__

    def __repr__(self):
        repr_list = []
        if hasattr(self, '__version__'):
            repr_list.append("version=" + repr(self.__version__))
        if hasattr(self, '__file__'):
            repr_list.append('from ' + repr(self.__file__))
        if repr_list:
            return '<ApiModule %r %s>' % (self.__name__, " ".join(repr_list))
        return '<ApiModule %r>' % (self.__name__,)

    def __makeattr(self, name):
        """lazily compute value for name or raise AttributeError if unknown."""
        target = None
        if '__onfirstaccess__' in self.__map__:
            target = self.__map__.pop('__onfirstaccess__')
            importobj(*target)()
        try:
            modpath, attrname = self.__map__[name]
        except KeyError:
            if target is not None and name != '__onfirstaccess__':
                # retry, onfirstaccess might have set attrs
                return getattr(self, name)
            raise AttributeError(name)
        else:
            result = importobj(modpath, attrname)
            setattr(self, name, result)
            try:
                del self.__map__[name]
            except KeyError:
                pass  # in a recursive-import situation a double-del can happen
            return result

    __getattr__ = __makeattr

    @property
    def __dict__(self):
        # force all the content of the module
        # to be loaded when __dict__ is read
        dictdescr = ModuleType.__dict__['__dict__']
        dict = dictdescr.__get__(self)
        if dict is not None:
            hasattr(self, 'some')
            for name in self.__all__:
                try:
                    self.__makeattr(name)
                except AttributeError:
                    pass
        return dict


def AliasModule(modname, modpath):
    mod = []
    pep302_noproxy_attributes = frozenset([
        '__name__', '__package__', '__loader__'
    ])

    def getmod():
        if not mod:
            mod.append(importobj(modpath, None))
        return mod[0]

    class AliasModule(ModuleType):

        def __repr__(self):
            return '<AliasModule %r for %r>' % (modname, modpath)

        # We have to use __getattribute__ and not __getattr__ in order to proxy
        # the __doc__ attribute. ModuleType instances always have a __doc__
        # attribute, even after delattr removes it from __dict__
        def __getattribute__(self, name):
            object_dict = ModuleType.__getattribute__(self, '__dict__')
            if name == '__package__':
                try:
                    return object_dict[name]
                except KeyError:
                    pass
                try:
                    name_attr = object_dict['__name__']
                except KeyError:
                    raise AttributeError(name)
                path_attr = getattr(getmod(), '__path__', None)
                if path_attr is not None:
                    # Module is a package so __package__ == __name__
                    package = name_attr
                else:
                    # Module is not a package so __package__ is parent __name__
                    # In Python 2 __package__ is None for a top-level module,
                    # but in Python 3 it is ''
                    package = name_attr.rpartition('.')[0] or sys.__package__
                setattr(self, name, package)
                return package
            if name in pep302_noproxy_attributes:
                try:
                    return object_dict[name]
                except KeyError:
                    raise AttributeError(name)
            return getattr(getmod(), name)

        def __setattr__(self, name, value):
            if name in pep302_noproxy_attributes:
                ModuleType.__setattr__(self, name, value)
            else:
                setattr(getmod(), name, value)

        def __delattr__(self, name):
            if name in pep302_noproxy_attributes:
                ModuleType.__delattr__(self, name)
            else:
                delattr(getmod(), name)

    return AliasModule(str(modname))
