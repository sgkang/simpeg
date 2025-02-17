from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
from .utils.code_utils import deprecate_class

from six import integer_types
from six import string_types
from collections import namedtuple
import warnings

import numpy as np
from numpy.polynomial import polynomial
import scipy.sparse as sp
from scipy.sparse.linalg import LinearOperator
from scipy.interpolate import UnivariateSpline
from scipy.constants import mu_0
from scipy.sparse import csr_matrix as csr

import properties
from discretize.tests import checkDerivative

from .utils import (
    setKwargs,
    mkvc,
    rotationMatrixFromNormals,
    Zero,
    Identity,
    sdiag,
    mat_utils,
    speye,
)


class IdentityMap(properties.HasProperties):
    """
    SimPEG Map
    """

    def __init__(self, mesh=None, nP=None, **kwargs):
        setKwargs(self, **kwargs)

        if nP is not None:
            if isinstance(nP, string_types):
                assert nP == "*", "nP must be an integer or '*', not {}".format(nP)
            assert isinstance(
                nP, integer_types + (np.int64,)
            ), "Number of parameters must be an integer. Not `{}`.".format(type(nP))
            nP = int(nP)
        elif mesh is not None:
            nP = mesh.nC
        else:
            nP = "*"

        self.mesh = mesh
        self._nP = nP

    @property
    def nP(self):
        """
        :rtype: int
        :return: number of parameters that the mapping accepts
        """
        if self._nP != "*":
            return int(self._nP)
        if self.mesh is None:
            return "*"
        return int(self.mesh.nC)

    @property
    def shape(self):
        """
        The default shape is (mesh.nC, nP) if the mesh is defined.
        If this is a meshless mapping (i.e. nP is defined independently)
        the shape will be the the shape (nP,nP).

        :rtype: tuple
        :return: shape of the operator as a tuple (int,int)
        """
        if self.mesh is None:
            return (self.nP, self.nP)
        return (self.mesh.nC, self.nP)

    def _transform(self, m):
        """
        Changes the model into the physical property.

        .. note::

            This can be called by the __mul__ property against a
            :meth:numpy.ndarray.

        :param numpy.ndarray m: model
        :rtype: numpy.ndarray
        :return: transformed model

        """
        return m

    def inverse(self, D):
        """
        Changes the physical property into the model.

        .. note::

            The *transformInverse* may not be easy to create in general.

        :param numpy.ndarray D: physical property
        :rtype: numpy.ndarray
        :return: model

        """
        raise NotImplementedError("The transformInverse is not implemented.")

    def deriv(self, m, v=None):
        """
        The derivative of the transformation.

        :param numpy.ndarray m: model
        :rtype: scipy.sparse.csr_matrix
        :return: derivative of transformed model

        """
        if v is not None:
            return v
        if isinstance(self.nP, integer_types):
            return sp.identity(self.nP)
        return Identity()

    def test(self, m=None, num=4, **kwargs):
        """Test the derivative of the mapping.

        :param numpy.ndarray m: model
        :param kwargs: key word arguments of
                       :meth:`discretize.tests.checkDerivative`
        :rtype: bool
        :return: passed the test?

        """
        print("Testing {0!s}".format(str(self)))
        if m is None:
            m = abs(np.random.rand(self.nP))
        if "plotIt" not in kwargs:
            kwargs["plotIt"] = False

        assert isinstance(
            self.nP, integer_types
        ), "nP must be an integer for {}".format(self.__class__.__name__)
        return checkDerivative(
            lambda m: [self * m, self.deriv(m)], m, num=num, **kwargs
        )

    def testVec(self, m=None, **kwargs):
        """Test the derivative of the mapping times a vector.

        :param numpy.ndarray m: model
        :param kwargs: key word arguments of
                       :meth:`discretize.tests.checkDerivative`
        :rtype: bool
        :return: passed the test?

        """
        print("Testing {0!s}".format(self))
        if m is None:
            m = abs(np.random.rand(self.nP))
        if "plotIt" not in kwargs:
            kwargs["plotIt"] = False
        return checkDerivative(
            lambda m: [self * m, lambda x: self.deriv(m, x)], m, num=4, **kwargs
        )

    def _assertMatchesPair(self, pair):
        assert (
            isinstance(self, pair)
            or isinstance(self, ComboMap)
            and isinstance(self.maps[0], pair)
        ), "Mapping object must be an instance of a {0!s} class.".format(pair.__name__)

    def __mul__(self, val):
        if isinstance(val, IdentityMap):
            if (
                not (self.shape[1] == "*" or val.shape[0] == "*")
                and not self.shape[1] == val.shape[0]
            ):
                raise ValueError(
                    "Dimension mismatch in {0!s} and {1!s}.".format(str(self), str(val))
                )
            return ComboMap([self, val])

        elif isinstance(val, np.ndarray):
            if not self.shape[1] == "*" and not self.shape[1] == val.shape[0]:
                raise ValueError(
                    "Dimension mismatch in {0!s} and np.ndarray{1!s}.".format(
                        str(self), str(val.shape)
                    )
                )
            return self._transform(val)

        elif isinstance(val, Zero):
            return Zero()

        raise Exception(
            "Unrecognized data type to multiply. Try a map or a numpy.ndarray!"
            "You used a {} of type {}".format(val, type(val))
        )

    def dot(self, val):
        return self.__mul__(val)

    def __matmul__(self, val):
        return self.__mul__(val)

    __numpy_ufunc__ = True

    def __add__(self, map2):
        return SumMap([self, map2])  # error-checking done inside of the SumMap

    def __str__(self):
        return "{0!s}({1!s},{2!s})".format(
            self.__class__.__name__, self.shape[0], self.shape[1]
        )

    def __len__(self):
        return 1


class ComboMap(IdentityMap):
    """
    Combination of various maps.

    The ComboMap holds the information for multiplying and combining
    maps. It also uses the chain rule to create the derivative.
    Remember, any time that you make your own combination of mappings
    be sure to test that the derivative is correct.

    """

    def __init__(self, maps, **kwargs):
        IdentityMap.__init__(self, None, **kwargs)

        self.maps = []
        for ii, m in enumerate(maps):
            assert isinstance(m, IdentityMap), "Unrecognized data type, "
            "inherit from an IdentityMap or ComboMap!"

            if (
                ii > 0
                and not (self.shape[1] == "*" or m.shape[0] == "*")
                and not self.shape[1] == m.shape[0]
            ):
                prev = self.maps[-1]

                raise ValueError(
                    "Dimension mismatch in map[{0!s}] ({1!s}, {2!s}) "
                    "and map[{3!s}] ({4!s}, {5!s}).".format(
                        prev.__class__.__name__,
                        prev.shape[0],
                        prev.shape[1],
                        m.__class__.__name__,
                        m.shape[0],
                        m.shape[1],
                    )
                )

            if np.any([isinstance(m, SumMap), isinstance(m, IdentityMap)]):
                self.maps += [m]
            elif isinstance(m, ComboMap):
                self.maps += m.maps
            else:
                raise ValueError("Map[{0!s}] not supported", m.__class__.__name__)

    @property
    def shape(self):
        return (self.maps[0].shape[0], self.maps[-1].shape[1])

    @property
    def nP(self):
        """Number of model properties.

        The number of cells in the
        last dimension of the mesh."""
        return self.maps[-1].nP

    def _transform(self, m):
        for map_i in reversed(self.maps):
            m = map_i * m
        return m

    def deriv(self, m, v=None):

        if v is not None:
            deriv = v
        else:
            deriv = 1

        mi = m
        for map_i in reversed(self.maps):
            deriv = map_i.deriv(mi) * deriv
            mi = map_i * mi
        return deriv

    def __str__(self):
        return "ComboMap[{0!s}]({1!s},{2!s})".format(
            " * ".join([m.__str__() for m in self.maps]), self.shape[0], self.shape[1]
        )

    def __len__(self):
        return len(self.maps)


class Projection(IdentityMap):
    """
    A map to rearrange / select parameters

    :param int nP: number of model parameters
    :param numpy.ndarray index: indices to select
    """

    def __init__(self, nP, index, **kwargs):
        assert isinstance(
            index, (np.ndarray, slice, list)
        ), "index must be a np.ndarray or slice, not {}".format(type(index))
        super(Projection, self).__init__(nP=nP, **kwargs)

        if isinstance(index, slice):
            index = list(range(*index.indices(self.nP)))

        if isinstance(index, np.ndarray):
            if index.dtype is np.dtype("bool"):
                index = np.where(index)[0]

        self.index = index
        self._shape = nI, nP = len(self.index), self.nP

        assert max(index) < nP, "maximum index must be less than {}".format(nP)

        # sparse projection matrix
        self.P = sp.csr_matrix((np.ones(nI), (range(nI), self.index)), shape=(nI, nP))

    def _transform(self, m):
        return m[self.index]

    @property
    def shape(self):
        """
        Shape of the matrix operation (number of indices x nP)
        """
        return self._shape

    def deriv(self, m, v=None):
        """
        :param numpy.ndarray m: model
        :rtype: scipy.sparse.csr_matrix
        :return: derivative of transformed model
        """

        if v is not None:
            return self.P * v
        return self.P


class SumMap(ComboMap):
    """
    A map to add model parameters contributing to the
    forward operation e.g. F(m) = F(g(x) + h(y))

    Assumes that the model vectors defined by g(x) and h(y)
    are equal in length.
    Allows to assume different things about the model m:
    i.e. parametric + voxel models
    """

    def __init__(self, maps, **kwargs):
        IdentityMap.__init__(self, None, **kwargs)

        self.maps = []
        for ii, m in enumerate(maps):
            if not isinstance(m, IdentityMap):
                raise TypeError(
                    "Unrecognized data type {}, inherit from an "
                    "IdentityMap!".format(type(m))
                )

            if (
                ii > 0
                and not (self.shape == "*" or m.shape == "*")
                and not self.shape == m.shape
            ):

                raise ValueError(
                    "Dimension mismatch in map[{0!s}] ({1!s}, {2!s}) "
                    "and map[{3!s}] ({4!s}, {5!s}).".format(
                        self.maps[0].__class__.__name__,
                        self.maps[0].shape[0],
                        self.maps[0].shape[1],
                        m.__class__.__name__,
                        m.shape[0],
                        m.shape[1],
                    )
                )

            self.maps += [m]

    @property
    def shape(self):
        return (self.maps[0].shape[0], self.maps[0].shape[1])

    @property
    def nP(self):
        """Number of model properties.

        The number of cells in the
        last dimension of the mesh."""
        return self.maps[-1].shape[1]

    def _transform(self, m):

        for ii, map_i in enumerate(self.maps):
            m0 = m.copy()
            m0 = map_i * m0

            if ii == 0:
                mout = m0
            else:
                mout += m0
        return mout

    def deriv(self, m, v=None):

        for ii, map_i in enumerate(self.maps):

            m0 = m.copy()

            if v is not None:
                deriv = v
            else:
                deriv = sp.eye(self.nP)

            deriv = map_i.deriv(m0, v=deriv)
            if ii == 0:
                sumDeriv = deriv
            else:
                sumDeriv += deriv

        return sumDeriv


class SurjectUnits(IdentityMap):
    """
    A map to group model cells into homogeneous units

    :param list indices: list of bool for each homogeneous unit
    """

    indices = properties.List(
        "list of indices for each unit to be surjected into",
        properties.Array(
            "indices for the unit to be mapped to", dtype=bool, shape=("*",)
        ),
        required=True,
    )

    # n_blocks = properties.Integer(
    #     "number of times to repeat the mapping", default=1, min=1
    # )

    def __init__(self, indices, **kwargs):
        super(SurjectUnits, self).__init__(**kwargs)
        self.indices = indices

    @property
    def P(self):
        if getattr(self, "_P", None) is None:
            # sparse projection matrix
            row = []
            col = []
            val = []
            for ii, ind in enumerate(self.indices):
                col += [ii] * ind.sum()
                row += np.where(ind)[0].tolist()
                val += [1] * ind.sum()

            self._P = sp.csr_matrix(
                (val, (row, col)), shape=(len(self.indices[0]), self.nP)
            )

            # self._P = sp.block_diag([P for ii in range(self.nBlock)])

        return self._P

    def _transform(self, m):
        return self.P * m

    @property
    def nP(self):
        return len(self.indices)

    @property
    def shape(self):
        """
        Shape of the matrix operation (number of indices x nP)
        """
        # return self.n_block*len(self.indices[0]), self.n_block*len(self.indices)
        return (len(self.indices[0]), self.nP)

    def deriv(self, m, v=None):
        """
        :param numpy.ndarray m: model
        :rtype: scipy.sparse.csr_matrix
        :return: derivative of transformed model
        """

        if v is not None:
            return self.P * v
        return self.P


class SphericalSystem(IdentityMap):
    """
    A vector map to spherical parameters of amplitude, theta and phi
    """

    def __init__(self, mesh=None, nP=None, **kwargs):
        super().__init__(mesh, nP, **kwargs)
        self.model = None

    def sphericalDeriv(self, model):

        if getattr(self, "model", None) is None:
            self.model = model

        if getattr(self, "_sphericalDeriv", None) is None or not all(
            self.model == model
        ):
            self.model = model

            # Do a double projection to make sure the parameters are bounded
            m_xyz = mat_utils.spherical2cartesian(model.reshape((-1, 3), order="F"))
            m_atp = mat_utils.cartesian2spherical(
                m_xyz.reshape((-1, 3), order="F")
            ).reshape((-1, 3), order="F")

            nC = m_atp[:, 0].shape[0]

            dm_dx = sp.hstack(
                [
                    sp.diags(np.cos(m_atp[:, 1]) * np.cos(m_atp[:, 2]), 0),
                    sp.diags(
                        -m_atp[:, 0] * np.sin(m_atp[:, 1]) * np.cos(m_atp[:, 2]), 0
                    ),
                    sp.diags(
                        -m_atp[:, 0] * np.cos(m_atp[:, 1]) * np.sin(m_atp[:, 2]), 0
                    ),
                ]
            )

            dm_dy = sp.hstack(
                [
                    sp.diags(np.cos(m_atp[:, 1]) * np.sin(m_atp[:, 2]), 0),
                    sp.diags(
                        -m_atp[:, 0] * np.sin(m_atp[:, 1]) * np.sin(m_atp[:, 2]), 0
                    ),
                    sp.diags(
                        m_atp[:, 0] * np.cos(m_atp[:, 1]) * np.cos(m_atp[:, 2]), 0
                    ),
                ]
            )

            dm_dz = sp.hstack(
                [
                    sp.diags(np.sin(m_atp[:, 1]), 0),
                    sp.diags(m_atp[:, 0] * np.cos(m_atp[:, 1]), 0),
                    csr((nC, nC)),
                ]
            )

            self._sphericalDeriv = sp.vstack([dm_dx, dm_dy, dm_dz])

        return self._sphericalDeriv

    def _transform(self, model):
        """

        :param model:
        :return:
        """
        return mat_utils.spherical2cartesian(model.reshape((-1, 3), order="F"))

    def inverse(self, model):
        """
        Cartesian to spherical.

        :param numpy.ndarray model: physical property in Cartesian
        :return: model

        """
        return mat_utils.cartesian2spherical(model.reshape((-1, 3), order="F"))

    @property
    def shape(self):
        """
        Shape of the matrix operation (number of indices x nP)
        """
        # return self.n_block*len(self.indices[0]), self.n_block*len(self.indices)
        return (self.nP, self.nP)

    def deriv(self, m, v=None):
        """
        :param numpy.ndarray m: model
        :rtype: scipy.sparse.csr_matrix
        :return: derivative of transformed model
        """

        if v is not None:
            return self.sphericalDeriv(m) * v
        return self.sphericalDeriv(m)


class Wires(object):
    def __init__(self, *args):
        for arg in args:
            assert (
                isinstance(arg, tuple)
                and len(arg) == 2
                and isinstance(arg[0], string_types)
                and
                # TODO: this should be extended to a slice.
                isinstance(arg[1], integer_types)
            ), (
                "Each wire needs to be a tuple: (name, length). "
                "You provided: {}".format(arg)
            )

        self._nP = int(np.sum([w[1] for w in args]))
        start = 0
        maps = []
        for arg in args:
            wire = Projection(self.nP, slice(start, start + arg[1]))
            setattr(self, arg[0], wire)
            maps += [(arg[0], wire)]
            start += arg[1]
        self.maps = maps

        self._tuple = namedtuple("Model", [w[0] for w in args])

    def __mul__(self, val):
        assert isinstance(val, np.ndarray)
        split = []
        for n, w in self.maps:
            split += [w * val]
        return self._tuple(*split)

    @property
    def nP(self):
        return self._nP


class SelfConsistentEffectiveMedium(IdentityMap, properties.HasProperties):
    """
        Two phase self-consistent effective medium theory mapping for
        ellipsoidal inclusions. The inversion model is the concentration
        (volume fraction) of the phase 2 material.

        The inversion model is :math:`\\varphi`. We solve for :math:`\sigma`
        given :math:`\sigma_0`, :math:`\sigma_1` and :math:`\\varphi` . Each of
        the following are implicit expressions of the effective conductivity.
        They are solved using a fixed point iteration.

        **Spherical Inclusions**

        If the shape of the inclusions are spheres, we use

        .. math::

            \sum_{j=1}^N (\sigma^* - \sigma_j)R^{j} = 0

        where :math:`j=[1,N]` is the each material phase, and N is the number
        of phases. Currently, the implementation is only set up for 2 phase
        materials, so we solve

        .. math::

            (1-\\varphi)(\sigma - \sigma_0)R^{(0)} + \\varphi(\sigma - \sigma_1)R^{(1)} = 0.

        Where :math:`R^{(j)}` is given by

        .. math::

            R^{(j)} = \\left[1 + \\frac{1}{3}\\frac{\sigma_j - \sigma}{\sigma} \\right]^{-1}.

        **Ellipsoids**

        .. todo::

            Aligned Ellipsoids have not yet been implemented, only randomly
            oriented ellipsoids

        If the inclusions are aligned ellipsoids, we solve

        .. math::

            \sum_{j=1}^N \\varphi_j (\Sigma^* - \sigma_j\mathbf{I}) \mathbf{R}^{j, *} = 0

        where

        .. math::

            \mathbf{R}^{(j, *)} = \left[ \mathbf{I} + \mathbf{A}_j {\Sigma^{*}}^{-1}(\sigma_j \mathbf{I} - \Sigma^*) \\right]^{-1}

        and the depolarization tensor :math:`\mathbf{A}_j` is given by

        .. math::

            \mathbf{A}^* = \\left[\\begin{array}{ccc}
                Q & 0 & 0 \\\\
                0 & Q & 0 \\\\
                0 & 0 & 1-2Q
            \end{array}\\right]

        for a spheroid aligned along the z-axis. For an oblate spheroid
        (:math:`\\alpha < 1`, pancake-like)

        .. math::

            Q = \\frac{1}{2}\\left(
                1 + \\frac{1}{\\alpha^2 - 1} \\left[
                    1 - \\frac{1}{\chi}\\tan^{-1}(\chi)
                \\right]
            \\right)

        where

        .. math::

            \chi = \sqrt{\\frac{1}{\\alpha^2} - 1}


        For reference, see
        `Torquato (2002), Random Heterogeneous Materials <https://link.springer.com/book/10.1007/978-1-4757-6355-3>`_


    """

    sigma0 = properties.Float(
        "physical property value for phase-0 material", min=0.0, required=True
    )  # this should also be allowed to be an array

    sigma1 = properties.Float(
        "physical property value for phase-1 material", min=0.0, required=True
    )

    alpha0 = properties.Float("aspect ratio of the phase-0 ellipsoids", default=1.0)

    alpha1 = properties.Float("aspect ratio of the phase-1 ellipsoids", default=1.0)

    orientation0 = properties.Vector3(
        "orientation of the phase-0 inclusions", default="Z"
    )

    orientation1 = properties.Vector3(
        "orientation of the phase-1 inclusions", default="Z"
    )

    random = properties.Bool(
        "are the inclusions randomly oriented (True) or preferentially "
        "aligned (False)?",
        default=True,
    )

    rel_tol = properties.Float(
        "relative tolerance for convergence for the fixed-point iteration", default=1e-3
    )

    maxIter = properties.Integer(
        "maximum number of iterations for the fixed point iteration " "calculation",
        default=50,
    )

    def __init__(self, mesh=None, nP=None, sigstart=None, **kwargs):
        self._sigstart = sigstart
        super(SelfConsistentEffectiveMedium, self).__init__(mesh, nP, **kwargs)

    @property
    def tol(self):
        """
        absolute tolerance for the convergence of the fixed point iteration
        calc
        """
        if getattr(self, "_tol", None) is None:
            self._tol = self.rel_tol * min(self.sigma0, self.sigma1)
        return self._tol

    @property
    def sigstart(self):
        """
        first guess for sigma
        """
        return self._sigstart

    def wiener_bounds(self, phi1):
        """Define Wenner Conductivity Bounds

        See Torquato, 2002
        """
        phi0 = 1.0 - phi1
        sigWup = phi0 * self.sigma0 + phi1 * self.sigma1
        sigWlo = 1.0 / (phi0 / self.sigma0 + phi1 / self.sigma1)
        W = np.array([sigWlo, sigWup])

        return W

    def hashin_shtrikman_bounds(self, phi1):
        """Hashin Shtrikman bounds

        See Torquato, 2002
        """
        # TODO: this should probably exsist on its own as a util

        phi0 = 1.0 - phi1
        sigWu = self.wiener_bounds(phi1)[1]
        sig_tilde = phi0 * self.sigma1 + phi1 * self.sigma0

        sigma_min = np.min([self.sigma0, self.sigma1])
        sigma_max = np.max([self.sigma0, self.sigma1])

        sigHSlo = sigWu - (
            (phi0 * phi1 * (self.sigma0 - self.sigma1) ** 2)
            / (sig_tilde + 2 * sigma_max)
        )
        sigHSup = sigWu - (
            (phi0 * phi1 * (self.sigma0 - self.sigma1) ** 2)
            / (sig_tilde + 2 * sigma_min)
        )

        return np.array([sigHSlo, sigHSup])

    def hashin_shtrikman_bounds_anisotropic(self, phi1):
        """Hashin Shtrikman bounds for anisotropic media

        See Torquato, 2002
        """
        phi0 = 1.0 - phi1
        sigWu = self.wiener_bounds(phi1)[1]

        sigma_min = np.min([self.sigma0, self.sigma1])
        sigma_max = np.max([self.sigma0, self.sigma1])

        phi_min = phi0 if self.sigma1 > self.sigma0 else phi1
        phi_max = phi1 if self.sigma1 > self.sigma0 else phi0

        amax = (
            -phi0
            * phi1
            * self.getA(
                self.alpha1 if self.sigma1 > self.sigma0 else self.alpha0,
                self.orientation1 if self.sigma1 > self.sigma0 else self.orientation0,
            )
        )
        I = np.eye(3)

        sigHSlo = sigWu * I + (
            (sigma_min - sigma_max) ** 2
            * amax
            * np.linalg.inv(sigma_min * I + (sigma_min - sigma_max) / phi_max * amax)
        )
        sigHSup = sigWu * I + (
            (sigma_max - sigma_min) ** 2
            * amax
            * np.linalg.inv(sigma_max * I + (sigma_max - sigma_min) / phi_min * amax)
        )

        return [sigHSlo, sigHSup]

    def getQ(self, alpha):
        """Geometric factor in the depolarization tensor"""
        if alpha < 1.0:  # oblate spheroid
            chi = np.sqrt((1.0 / alpha ** 2.0) - 1)
            return (
                1.0
                / 2.0
                * (1 + 1.0 / (alpha ** 2.0 - 1) * (1.0 - np.arctan(chi) / chi))
            )
        elif alpha > 1.0:  # prolate spheroid
            chi = np.sqrt(1 - (1.0 / alpha ** 2.0))
            return (
                1.0
                / 2.0
                * (
                    1
                    + 1.0
                    / (alpha ** 2.0 - 1)
                    * (1.0 - 1.0 / (2.0 * chi) * np.log((1 + chi) / (1 - chi)))
                )
            )
        elif alpha == 1:  # sphere
            return 1.0 / 3.0

    def getA(self, alpha, orientation):
        """Depolarization tensor"""
        Q = self.getQ(alpha)
        A = np.diag([Q, Q, 1 - 2 * Q])
        R = rotationMatrixFromNormals(np.r_[0.0, 0.0, 1.0], orientation)
        return (R.T).dot(A).dot(R)

    def getR(self, sj, se, alpha, orientation=None):
        """Electric field concentration tensor"""
        if self.random is True:  # isotropic
            if alpha == 1.0:
                return 3.0 * se / (2.0 * se + sj)
            Q = self.getQ(alpha)
            return (
                se
                / 3.0
                * (2.0 / (se + Q * (sj - se)) + 1.0 / (sj - 2.0 * Q * (sj - se)))
            )
        else:  # anisotropic
            if orientation is None:
                raise Exception("orientation must be provided if random=False")
            I = np.eye(3)
            seinv = np.linalg.inv(se)
            Rinv = I + self.getA(alpha, orientation) * seinv * (sj * I - se)
            return np.linalg.inv(Rinv)

    def getdR(self, sj, se, alpha, orientation=None):
        """
        Derivative of the electric field concentration tensor with respect
        to the concentration of the second phase material.
        """
        if self.random is True:
            if alpha == 1.0:
                return 3.0 / (2.0 * se + sj) - 6.0 * se / (2.0 * se + sj) ** 2
            Q = self.getQ(alpha)
            return (
                1
                / 3
                * (
                    2.0 / (se + Q * (sj - se))
                    + 1.0 / (sj - 2.0 * Q * (sj - se))
                    + se
                    * (
                        -2 * (1 - Q) / (se + Q * (sj - se)) ** 2
                        - 2 * Q / (sj - 2.0 * Q * (sj - se)) ** 2
                    )
                )
            )
        else:
            if orientation is None:
                raise Exception("orientation must be provided if random=False")
            raise NotImplementedError

    def _sc2phaseEMTSpheroidstransform(self, phi1):
        """
        Self Consistent Effective Medium Theory Model Transform,
        alpha = aspect ratio (c/a <= 1)
        """

        if not (np.all(0 <= phi1) and np.all(phi1 <= 1)):
            warnings.warn("there are phis outside bounds of 0 and 1")
            phi1 = np.median(np.c_[phi1 * 0, phi1, phi1 * 0 + 1.0])

        phi0 = 1.0 - phi1

        # starting guess
        if self.sigstart is None:
            sige1 = np.mean(self.wiener_bounds(phi1))
        else:
            sige1 = self.sigstart

        if self.random is False:
            sige1 = sige1 * np.eye(3)

        for i in range(self.maxIter):
            R0 = self.getR(self.sigma0, sige1, self.alpha0, self.orientation0)
            R1 = self.getR(self.sigma1, sige1, self.alpha1, self.orientation1)

            den = phi0 * R0 + phi1 * R1
            num = phi0 * self.sigma0 * R0 + phi1 * self.sigma1 * R1

            if self.random is True:
                sige2 = num / den
                relerr = np.abs(sige2 - sige1)
            else:
                sige2 = num * np.linalg.inv(den)
                relerr = np.linalg.norm(np.abs(sige2 - sige1).flatten(), np.inf)

            if np.all(relerr <= self.tol):
                if self.sigstart is None:
                    self._sigstart = (
                        sige2  # store as a starting point for the next time around
                    )
                return sige2

            sige1 = sige2
        # TODO: make this a proper warning, and output relevant info (sigma0, sigma1, phi, sigstart, and relerr)
        warnings.warn("Maximum number of iterations reached")

        return sige2

    def _sc2phaseEMTSpheroidsinversetransform(self, sige):

        R0 = self.getR(self.sigma0, sige, self.alpha0, self.orientation0)
        R1 = self.getR(self.sigma1, sige, self.alpha1, self.orientation1)

        num = -(self.sigma0 - sige) * R0
        den = (self.sigma1 - sige) * R1 - (self.sigma0 - sige) * R0

        return num / den

    def _sc2phaseEMTSpheroidstransformDeriv(self, sige, phi1):

        phi0 = 1.0 - phi1

        R0 = self.getR(self.sigma0, sige, self.alpha0, self.orientation0)
        R1 = self.getR(self.sigma1, sige, self.alpha1, self.orientation1)

        dR0 = self.getdR(self.sigma0, sige, self.alpha0, self.orientation0)
        dR1 = self.getdR(self.sigma1, sige, self.alpha1, self.orientation1)

        num = (sige - self.sigma0) * R0 - (sige - self.sigma1) * R1
        den = phi0 * (R0 + (sige - self.sigma0) * dR0) + phi1 * (
            R1 + (sige - self.sigma1) * dR1
        )

        return sdiag(num / den)

    def _transform(self, m):
        return self._sc2phaseEMTSpheroidstransform(m)

    def deriv(self, m):
        """
        Derivative of the effective conductivity with respect to the
        volume fraction of phase 2 material
        """
        sige = self._transform(m)
        return self._sc2phaseEMTSpheroidstransformDeriv(sige, m)

    def inverse(self, sige):
        """
        Compute the concentration given the effective conductivity
        """
        return self._sc2phaseEMTSpheroidsinversetransform(sige)


###############################################################################
#                                                                             #
#                          Mesh Independent Maps                              #
#                                                                             #
###############################################################################


class ExpMap(IdentityMap):
    """
    Electrical conductivity varies over many orders of magnitude, so it is
    a common technique when solving the inverse problem to parameterize and
    optimize in terms of log conductivity. This makes sense not only
    because it ensures all conductivities will be positive, but because
    this is fundamentally the space where conductivity
    lives (i.e. it varies logarithmically).

    Changes the model into the physical property.

    A common example of this is to invert for electrical conductivity
    in log space. In this case, your model will be log(sigma) and to
    get back to sigma, you can take the exponential:

    .. math::

        m = \log{\sigma}

        \exp{m} = \exp{\log{\sigma}} = \sigma
    """

    def __init__(self, mesh=None, nP=None, **kwargs):
        super(ExpMap, self).__init__(mesh=mesh, nP=nP, **kwargs)

    def _transform(self, m):
        return np.exp(mkvc(m))

    def inverse(self, D):
        """
        :param numpy.ndarray D: physical property
        :rtype: numpy.ndarray
        :return: model

        The *transformInverse* changes the physical property into the
        model.

        .. math::

            m = \log{\sigma}

        """
        return np.log(mkvc(D))

    def deriv(self, m, v=None):
        """
        :param numpy.ndarray m: model
        :rtype: scipy.sparse.csr_matrix
        :return: derivative of transformed model

        The *transform* changes the model into the physical property.
        The *transformDeriv* provides the derivative of the *transform*.

        If the model *transform* is:

        .. math::

            m = \log{\sigma}

            \exp{m} = \exp{\log{\sigma}} = \sigma

        Then the derivative is:

        .. math::

            \\frac{\partial \exp{m}}{\partial m} = \\text{sdiag}(\exp{m})
        """
        deriv = sdiag(np.exp(mkvc(m)))
        if v is not None:
            return deriv * v
        return deriv


class ReciprocalMap(IdentityMap):
    """
    Reciprocal mapping. For example, electrical resistivity and
    conductivity.

    .. math::

        \\rho = \\frac{1}{\sigma}

    """

    def __init__(self, mesh=None, nP=None, **kwargs):
        super(ReciprocalMap, self).__init__(mesh=mesh, nP=nP, **kwargs)

    def _transform(self, m):
        return 1.0 / mkvc(m)

    def inverse(self, D):
        return 1.0 / mkvc(D)

    def deriv(self, m, v=None):
        # TODO: if this is a tensor, you might have a problem.
        deriv = sdiag(-mkvc(m) ** (-2))
        if v is not None:
            return deriv * v
        return deriv


class LogMap(IdentityMap):
    """
    Changes the model into the physical property.

    If \\(p\\) is the physical property and \\(m\\) is the model, then

    .. math::

        p = \\log(m)

    and

    .. math::

        m = \\exp(p)

    NOTE: If you have a model which is log conductivity
    (ie. \\(m = \\log(\\sigma)\\)),
    you should be using an ExpMap

    """

    def __init__(self, mesh=None, nP=None, **kwargs):
        super(LogMap, self).__init__(mesh=mesh, nP=nP, **kwargs)

    def _transform(self, m):
        return np.log(mkvc(m))

    def deriv(self, m, v=None):
        mod = mkvc(m)
        deriv = np.zeros(mod.shape)
        tol = 1e-16  # zero
        ind = np.greater_equal(np.abs(mod), tol)
        deriv[ind] = 1.0 / mod[ind]
        if v is not None:
            return sdiag(deriv) * v
        return sdiag(deriv)

    def inverse(self, m):
        return np.exp(mkvc(m))


class ChiMap(IdentityMap):
    """Chi Map

    Convert Magnetic Susceptibility to Magnetic Permeability.

    .. math::

        \mu(m) = \mu_0 (1 + \chi(m))

    """

    def __init__(self, mesh=None, nP=None, **kwargs):
        super(ChiMap, self).__init__(mesh=mesh, nP=nP, **kwargs)

    def _transform(self, m):
        return mu_0 * (1 + m)

    def deriv(self, m, v=None):
        if v is not None:
            return mu_0 * v
        return mu_0 * sp.eye(self.nP)

    def inverse(self, m):
        return m / mu_0 - 1


class MuRelative(IdentityMap):
    """
    Invert for relative permeability

    .. math::

        \mu(m) = \mu_0 * \mathbf{m}
    """

    def __init__(self, mesh=None, nP=None, **kwargs):
        super(MuRelative, self).__init__(mesh=mesh, nP=nP, **kwargs)

    def _transform(self, m):
        return mu_0 * m

    def deriv(self, m, v=None):
        if v is not None:
            return mu_0 * v
        return mu_0 * sp.eye(self.nP)

    def inverse(self, m):
        return 1.0 / mu_0 * m


class Weighting(IdentityMap):
    """
    Model weight parameters.
    """

    def __init__(self, mesh=None, nP=None, weights=None, **kwargs):

        if "nC" in kwargs:
            raise TypeError(
                "`nC` has been removed. Use `nP` to set the number of model "
                "parameters."
            )

        super(Weighting, self).__init__(mesh=mesh, nP=nP, **kwargs)

        if weights is None:
            weights = np.ones(self.nP)

        self.weights = np.array(weights, dtype=float)

    @property
    def shape(self):
        return (self.nP, self.nP)

    @property
    def P(self):
        return sdiag(self.weights)

    def _transform(self, m):
        return self.weights * m

    def inverse(self, D):
        return self.weights ** (-1.0) * D

    def deriv(self, m, v=None):
        if v is not None:
            return self.weights * v
        return self.P


class ComplexMap(IdentityMap):
    """ComplexMap

    default nP is nC in the mesh times 2 [real, imag]

    """

    def __init__(self, mesh=None, nP=None, **kwargs):
        super(ComplexMap, self).__init__(mesh=mesh, nP=nP, **kwargs)
        if nP is not None:
            assert nP % 2 == 0, "nP must be even."
        self._nP = nP or int(self.mesh.nC * 2)

    @property
    def nP(self):
        return self._nP

    @property
    def shape(self):
        return (int(self.nP / 2), self.nP)

    def _transform(self, m):
        nC = self.mesh.nC
        return m[:nC] + m[nC:] * 1j

    def deriv(self, m, v=None):
        nC = self.shape[0]
        shp = (nC, nC * 2)

        def fwd(v):
            return v[:nC] + v[nC:] * 1j

        def adj(v):
            return np.r_[v.real, v.imag]

        if v is not None:
            return LinearOperator(shp, matvec=fwd, rmatvec=adj) * v
        return LinearOperator(shp, matvec=fwd, rmatvec=adj)

    # inverse = deriv


###############################################################################
#                                                                             #
#                 Surjection, Injection and Interpolation Maps                #
#                                                                             #
###############################################################################


class SurjectFull(IdentityMap):
    """
    SurjectFull

    Given a scalar, the SurjectFull maps the value to the
    full model space.
    """

    def __init__(self, mesh, **kwargs):
        IdentityMap.__init__(self, mesh, **kwargs)

    @property
    def nP(self):
        return 1

    def _transform(self, m):
        """
        :param m: model (scalar)
        :rtype: numpy.ndarray
        :return: transformed model
        """
        return np.ones(self.mesh.nC) * m

    def deriv(self, m, v=None):
        """
        :param numpy.ndarray m: model
        :rtype: numpy.ndarray
        :return: derivative of transformed model
        """
        deriv = sp.csr_matrix(np.ones([self.mesh.nC, 1]))
        if v is not None:
            return deriv * v
        return deriv


class SurjectVertical1D(IdentityMap):
    """SurjectVertical1DMap

    Given a 1D vector through the last dimension
    of the mesh, this will extend to the full
    model space.
    """

    def __init__(self, mesh, **kwargs):
        IdentityMap.__init__(self, mesh, **kwargs)

    @property
    def nP(self):
        """Number of model properties.

        The number of cells in the
        last dimension of the mesh."""
        # in discretize 0.7 the int conversion will not be required
        return int(self.mesh.vnC[self.mesh.dim - 1])

    def _transform(self, m):
        """
        :param numpy.ndarray m: model
        :rtype: numpy.ndarray
        :return: transformed model
        """
        repNum = np.prod(self.mesh.vnC[: self.mesh.dim - 1])
        return mkvc(m).repeat(repNum)

    def deriv(self, m, v=None):
        """
        :param numpy.ndarray m: model
        :rtype: scipy.sparse.csr_matrix
        :return: derivative of transformed model
        """
        repNum = np.prod(self.mesh.vnC[: self.mesh.dim - 1])
        repVec = sp.csr_matrix(
            (np.ones(repNum), (range(repNum), np.zeros(repNum))), shape=(repNum, 1)
        )
        deriv = sp.kron(sp.identity(self.nP), repVec)
        if v is not None:
            return deriv * v
        return deriv


class Surject2Dto3D(IdentityMap):
    """Map2Dto3D

    Given a 2D vector, this will extend to the full
    3D model space.
    """

    normal = "Y"  #: The normal

    def __init__(self, mesh, **kwargs):
        assert mesh.dim == 3, "Surject2Dto3D Only works for a 3D Mesh"
        IdentityMap.__init__(self, mesh, **kwargs)
        assert self.normal in ["X", "Y", "Z"], 'For now, only "Y" normal is supported'

    @property
    def nP(self):
        """Number of model properties.

        The number of cells in the
        last dimension of the mesh."""
        if self.normal == "Z":
            return self.mesh.nCx * self.mesh.nCy
        elif self.normal == "Y":
            return self.mesh.nCx * self.mesh.nCz
        elif self.normal == "X":
            return self.mesh.nCy * self.mesh.nCz

    def _transform(self, m):
        """
        :param numpy.ndarray m: model
        :rtype: numpy.ndarray
        :return: transformed model
        """
        m = mkvc(m)
        if self.normal == "Z":
            return mkvc(
                m.reshape(self.mesh.vnC[:2], order="F")[:, :, np.newaxis].repeat(
                    self.mesh.nCz, axis=2
                )
            )
        elif self.normal == "Y":
            return mkvc(
                m.reshape(self.mesh.vnC[::2], order="F")[:, np.newaxis, :].repeat(
                    self.mesh.nCy, axis=1
                )
            )
        elif self.normal == "X":
            return mkvc(
                m.reshape(self.mesh.vnC[1:], order="F")[np.newaxis, :, :].repeat(
                    self.mesh.nCx, axis=0
                )
            )

    def deriv(self, m, v=None):
        """
        :param numpy.ndarray m: model
        :rtype: scipy.sparse.csr_matrix
        :return: derivative of transformed model
        """
        inds = self * np.arange(self.nP)
        nC, nP = self.mesh.nC, self.nP
        P = sp.csr_matrix((np.ones(nC), (range(nC), inds)), shape=(nC, nP))
        if v is not None:
            return P * v
        return P


class Mesh2Mesh(IdentityMap):
    """
    Takes a model on one mesh are translates it to another mesh.
    """

    indActive = properties.Array("active indices on target mesh", dtype=bool)

    def __init__(self, meshes, **kwargs):
        setKwargs(self, **kwargs)

        assert type(meshes) is list, "meshes must be a list of two meshes"
        assert len(meshes) == 2, "meshes must be a list of two meshes"
        assert (
            meshes[0].dim == meshes[1].dim
        ), "The two meshes must be the same dimension"

        self.mesh = meshes[0]
        self.mesh2 = meshes[1]

    @property
    def P(self):
        if getattr(self, "_P", None) is None:
            self._P = self.mesh2.getInterpolationMat(
                self.mesh.gridCC[self.indActive, :]
                if self.indActive is not None
                else self.mesh.gridCC,
                "CC",
                zerosOutside=True,
            )
        return self._P

    @property
    def shape(self):
        """Number of parameters in the model."""
        if self.indActive is not None:
            return (self.indActive.sum(), self.mesh2.nC)
        return (self.mesh.nC, self.mesh2.nC)

    @property
    def nP(self):
        """Number of parameters in the model."""
        return self.mesh2.nC

    def _transform(self, m):
        return self.P * m

    def deriv(self, m, v=None):
        if v is not None:
            return self.P * v
        return self.P


class InjectActiveCells(IdentityMap):
    """
    Active model parameters.

    """

    indActive = None  #: Active Cells
    valInactive = None  #: Values of inactive Cells

    def __init__(self, mesh, indActive, valInactive, nC=None):
        self.mesh = mesh

        self.nC = nC or mesh.nC

        if indActive.dtype is not bool:
            z = np.zeros(self.nC, dtype=bool)
            z[indActive] = True
            indActive = z
        self.indActive = indActive
        self.indInactive = np.logical_not(indActive)
        if np.isscalar(valInactive):
            self.valInactive = np.ones(self.nC) * float(valInactive)
        else:
            self.valInactive = np.ones(self.nC)
            self.valInactive[self.indInactive] = valInactive.copy()

        self.valInactive[self.indActive] = 0

        inds = np.nonzero(self.indActive)[0]
        self.P = sp.csr_matrix(
            (np.ones(inds.size), (inds, range(inds.size))), shape=(self.nC, self.nP)
        )

    @property
    def shape(self):
        return (self.nC, self.nP)

    @property
    def nP(self):
        """Number of parameters in the model."""
        return int(self.indActive.sum())

    def _transform(self, m):
        return self.P * m + self.valInactive

    def inverse(self, D):
        return self.P.T * D

    def deriv(self, m, v=None):
        if v is not None:
            return self.P * v
        return self.P


###############################################################################
#                                                                             #
#                             Parametric Maps                                 #
#                                                                             #
###############################################################################


class ParametricCircleMap(IdentityMap):
    """ParametricCircleMap

    Parameterize the model space using a circle in a wholespace.

    .. math::

        \sigma(m) = \sigma_1 + (\sigma_2 - \sigma_1)\left(
        \\arctan\left(100*\sqrt{(\\vec{x}-x_0)^2 + (\\vec{y}-y_0)}-r
        \\right) \pi^{-1} + 0.5\\right)

    Define the model as:

    .. math::

        m = [\sigma_1, \sigma_2, x_0, y_0, r]

    """

    slope = 1e-1

    def __init__(self, mesh, logSigma=True):
        assert mesh.dim == 2, (
            "Working for a 2D mesh only right now. "
            "But it isn't that hard to change.. :)"
        )
        IdentityMap.__init__(self, mesh)
        # TODO: this should be done through a composition with and ExpMap
        self.logSigma = logSigma

    @property
    def nP(self):
        return 5

    def _transform(self, m):
        a = self.slope
        sig1, sig2, x, y, r = m[0], m[1], m[2], m[3], m[4]
        if self.logSigma:
            sig1, sig2 = np.exp(sig1), np.exp(sig2)
        X = self.mesh.gridCC[:, 0]
        Y = self.mesh.gridCC[:, 1]
        return sig1 + (sig2 - sig1) * (
            np.arctan(a * (np.sqrt((X - x) ** 2 + (Y - y) ** 2) - r)) / np.pi + 0.5
        )

    def deriv(self, m, v=None):
        a = self.slope
        sig1, sig2, x, y, r = m[0], m[1], m[2], m[3], m[4]
        if self.logSigma:
            sig1, sig2 = np.exp(sig1), np.exp(sig2)
        X = self.mesh.gridCC[:, 0]
        Y = self.mesh.gridCC[:, 1]
        if self.logSigma:
            g1 = (
                -(
                    np.arctan(a * (-r + np.sqrt((X - x) ** 2 + (Y - y) ** 2))) / np.pi
                    + 0.5
                )
                * sig1
                + sig1
            )
            g2 = (
                np.arctan(a * (-r + np.sqrt((X - x) ** 2 + (Y - y) ** 2))) / np.pi + 0.5
            ) * sig2
        else:
            g1 = (
                -(
                    np.arctan(a * (-r + np.sqrt((X - x) ** 2 + (Y - y) ** 2))) / np.pi
                    + 0.5
                )
                + 1.0
            )
            g2 = (
                np.arctan(a * (-r + np.sqrt((X - x) ** 2 + (Y - y) ** 2))) / np.pi + 0.5
            )

        g3 = (
            a
            * (-X + x)
            * (-sig1 + sig2)
            / (
                np.pi
                * (a ** 2 * (-r + np.sqrt((X - x) ** 2 + (Y - y) ** 2)) ** 2 + 1)
                * np.sqrt((X - x) ** 2 + (Y - y) ** 2)
            )
        )

        g4 = (
            a
            * (-Y + y)
            * (-sig1 + sig2)
            / (
                np.pi
                * (a ** 2 * (-r + np.sqrt((X - x) ** 2 + (Y - y) ** 2)) ** 2 + 1)
                * np.sqrt((X - x) ** 2 + (Y - y) ** 2)
            )
        )

        g5 = (
            -a
            * (-sig1 + sig2)
            / (np.pi * (a ** 2 * (-r + np.sqrt((X - x) ** 2 + (Y - y) ** 2)) ** 2 + 1))
        )

        if v is not None:
            return sp.csr_matrix(np.c_[g1, g2, g3, g4, g5]) * v
        return sp.csr_matrix(np.c_[g1, g2, g3, g4, g5])


class ParametricPolyMap(IdentityMap):

    """PolyMap

    Parameterize the model space using a polynomials in a wholespace.

    .. math::

        y = \mathbf{V} c

    Define the model as:

    .. math::

        m = [\sigma_1, \sigma_2, c]

    Can take in an actInd vector to account for topography.

    """

    def __init__(self, mesh, order, logSigma=True, normal="X", actInd=None):
        IdentityMap.__init__(self, mesh)
        self.logSigma = logSigma
        self.order = order
        self.normal = normal
        self.actInd = actInd

        if getattr(self, "actInd", None) is None:
            self.actInd = list(range(self.mesh.nC))
            self.nC = self.mesh.nC

        else:
            self.nC = len(self.actInd)

    slope = 1e4

    @property
    def shape(self):
        return (self.nC, self.nP)

    @property
    def nP(self):
        if np.isscalar(self.order):
            nP = self.order + 3
        else:
            nP = (self.order[0] + 1) * (self.order[1] + 1) + 2
        return nP

    def _transform(self, m):
        # Set model parameters
        alpha = self.slope
        sig1, sig2 = m[0], m[1]
        c = m[2:]
        if self.logSigma:
            sig1, sig2 = np.exp(sig1), np.exp(sig2)

        # 2D
        if self.mesh.dim == 2:
            X = self.mesh.gridCC[self.actInd, 0]
            Y = self.mesh.gridCC[self.actInd, 1]
            if self.normal == "X":
                f = polynomial.polyval(Y, c) - X
            elif self.normal == "Y":
                f = polynomial.polyval(X, c) - Y
            else:
                raise (Exception("Input for normal = X or Y or Z"))

        # 3D
        elif self.mesh.dim == 3:
            X = self.mesh.gridCC[self.actInd, 0]
            Y = self.mesh.gridCC[self.actInd, 1]
            Z = self.mesh.gridCC[self.actInd, 2]

            if self.normal == "X":
                f = (
                    polynomial.polyval2d(
                        Y, Z, c.reshape((self.order[0] + 1, self.order[1] + 1))
                    )
                    - X
                )
            elif self.normal == "Y":
                f = (
                    polynomial.polyval2d(
                        X, Z, c.reshape((self.order[0] + 1, self.order[1] + 1))
                    )
                    - Y
                )
            elif self.normal == "Z":
                f = (
                    polynomial.polyval2d(
                        X, Y, c.reshape((self.order[0] + 1, self.order[1] + 1))
                    )
                    - Z
                )
            else:
                raise (Exception("Input for normal = X or Y or Z"))

        else:
            raise (Exception("Only supports 2D"))

        return sig1 + (sig2 - sig1) * (np.arctan(alpha * f) / np.pi + 0.5)

    def deriv(self, m, v=None):
        alpha = self.slope
        sig1, sig2, c = m[0], m[1], m[2:]
        if self.logSigma:
            sig1, sig2 = np.exp(sig1), np.exp(sig2)

        # 2D
        if self.mesh.dim == 2:
            X = self.mesh.gridCC[self.actInd, 0]
            Y = self.mesh.gridCC[self.actInd, 1]

            if self.normal == "X":
                f = polynomial.polyval(Y, c) - X
                V = polynomial.polyvander(Y, len(c) - 1)
            elif self.normal == "Y":
                f = polynomial.polyval(X, c) - Y
                V = polynomial.polyvander(X, len(c) - 1)
            else:
                raise (Exception("Input for normal = X or Y or Z"))

        # 3D
        elif self.mesh.dim == 3:
            X = self.mesh.gridCC[self.actInd, 0]
            Y = self.mesh.gridCC[self.actInd, 1]
            Z = self.mesh.gridCC[self.actInd, 2]

            if self.normal == "X":
                f = (
                    polynomial.polyval2d(
                        Y, Z, c.reshape((self.order[0] + 1, self.order[1] + 1))
                    )
                    - X
                )
                V = polynomial.polyvander2d(Y, Z, self.order)
            elif self.normal == "Y":
                f = (
                    polynomial.polyval2d(
                        X, Z, c.reshape((self.order[0] + 1, self.order[1] + 1))
                    )
                    - Y
                )
                V = polynomial.polyvander2d(X, Z, self.order)
            elif self.normal == "Z":
                f = (
                    polynomial.polyval2d(
                        X, Y, c.reshape((self.order[0] + 1, self.order[1] + 1))
                    )
                    - Z
                )
                V = polynomial.polyvander2d(X, Y, self.order)
            else:
                raise (Exception("Input for normal = X or Y or Z"))

        if self.logSigma:
            g1 = -(np.arctan(alpha * f) / np.pi + 0.5) * sig1 + sig1
            g2 = (np.arctan(alpha * f) / np.pi + 0.5) * sig2
        else:
            g1 = -(np.arctan(alpha * f) / np.pi + 0.5) + 1.0
            g2 = np.arctan(alpha * f) / np.pi + 0.5

        g3 = sdiag(alpha * (sig2 - sig1) / (1.0 + (alpha * f) ** 2) / np.pi) * V

        if v is not None:
            return sp.csr_matrix(np.c_[g1, g2, g3]) * v
        return sp.csr_matrix(np.c_[g1, g2, g3])


class ParametricSplineMap(IdentityMap):

    """SplineMap

    Parameterize the boundary of two geological units using
    a spline interpolation

    .. math::

        g = f(x)-y

    Define the model as:

    .. math::

        m = [\sigma_1, \sigma_2, y]

    """

    slope = 1e4

    def __init__(self, mesh, pts, ptsv=None, order=3, logSigma=True, normal="X"):
        IdentityMap.__init__(self, mesh)
        self.logSigma = logSigma
        self.order = order
        self.normal = normal
        self.pts = pts
        self.npts = np.size(pts)
        self.ptsv = ptsv
        self.spl = None

    @property
    def nP(self):
        if self.mesh.dim == 2:
            return np.size(self.pts) + 2
        elif self.mesh.dim == 3:
            return np.size(self.pts) * 2 + 2
        else:
            raise (Exception("Only supports 2D and 3D"))

    def _transform(self, m):
        # Set model parameters
        alpha = self.slope
        sig1, sig2 = m[0], m[1]
        c = m[2:]
        if self.logSigma:
            sig1, sig2 = np.exp(sig1), np.exp(sig2)
        # 2D
        if self.mesh.dim == 2:
            X = self.mesh.gridCC[:, 0]
            Y = self.mesh.gridCC[:, 1]
            self.spl = UnivariateSpline(self.pts, c, k=self.order, s=0)
            if self.normal == "X":
                f = self.spl(Y) - X
            elif self.normal == "Y":
                f = self.spl(X) - Y
            else:
                raise (Exception("Input for normal = X or Y or Z"))

        # 3D:
        # Comments:
        # Make two spline functions and link them using linear interpolation.
        # This is not quite direct extension of 2D to 3D case
        # Using 2D interpolation  is possible

        elif self.mesh.dim == 3:
            X = self.mesh.gridCC[:, 0]
            Y = self.mesh.gridCC[:, 1]
            Z = self.mesh.gridCC[:, 2]

            npts = np.size(self.pts)
            if np.mod(c.size, 2):
                raise (Exception("Put even points!"))

            self.spl = {
                "splb": UnivariateSpline(self.pts, c[:npts], k=self.order, s=0),
                "splt": UnivariateSpline(self.pts, c[npts:], k=self.order, s=0),
            }

            if self.normal == "X":
                zb = self.ptsv[0]
                zt = self.ptsv[1]
                flines = (self.spl["splt"](Y) - self.spl["splb"](Y)) * (Z - zb) / (
                    zt - zb
                ) + self.spl["splb"](Y)
                f = flines - X
            # elif self.normal =='Y':
            # elif self.normal =='Z':
            else:
                raise (Exception("Input for normal = X or Y or Z"))
        else:
            raise (Exception("Only supports 2D and 3D"))

        return sig1 + (sig2 - sig1) * (np.arctan(alpha * f) / np.pi + 0.5)

    def deriv(self, m, v=None):
        alpha = self.slope
        sig1, sig2, c = m[0], m[1], m[2:]
        if self.logSigma:
            sig1, sig2 = np.exp(sig1), np.exp(sig2)
        # 2D
        if self.mesh.dim == 2:
            X = self.mesh.gridCC[:, 0]
            Y = self.mesh.gridCC[:, 1]

            if self.normal == "X":
                f = self.spl(Y) - X
            elif self.normal == "Y":
                f = self.spl(X) - Y
            else:
                raise (Exception("Input for normal = X or Y or Z"))
        # 3D
        elif self.mesh.dim == 3:
            X = self.mesh.gridCC[:, 0]
            Y = self.mesh.gridCC[:, 1]
            Z = self.mesh.gridCC[:, 2]

            if self.normal == "X":
                zb = self.ptsv[0]
                zt = self.ptsv[1]
                flines = (self.spl["splt"](Y) - self.spl["splb"](Y)) * (Z - zb) / (
                    zt - zb
                ) + self.spl["splb"](Y)
                f = flines - X
            # elif self.normal =='Y':
            # elif self.normal =='Z':
            else:
                raise (Exception("Not Implemented for Y and Z, your turn :)"))

        if self.logSigma:
            g1 = -(np.arctan(alpha * f) / np.pi + 0.5) * sig1 + sig1
            g2 = (np.arctan(alpha * f) / np.pi + 0.5) * sig2
        else:
            g1 = -(np.arctan(alpha * f) / np.pi + 0.5) + 1.0
            g2 = np.arctan(alpha * f) / np.pi + 0.5

        if self.mesh.dim == 2:
            g3 = np.zeros((self.mesh.nC, self.npts))
            if self.normal == "Y":
                # Here we use perturbation to compute sensitivity
                # TODO: bit more generalization of this ...
                # Modfications for X and Z directions ...
                for i in range(np.size(self.pts)):
                    ctemp = c[i]
                    ind = np.argmin(abs(self.mesh.vectorCCy - ctemp))
                    ca = c.copy()
                    cb = c.copy()
                    dy = self.mesh.hy[ind] * 1.5
                    ca[i] = ctemp + dy
                    cb[i] = ctemp - dy
                    spla = UnivariateSpline(self.pts, ca, k=self.order, s=0)
                    splb = UnivariateSpline(self.pts, cb, k=self.order, s=0)
                    fderiv = (spla(X) - splb(X)) / (2 * dy)
                    g3[:, i] = (
                        sdiag(alpha * (sig2 - sig1) / (1.0 + (alpha * f) ** 2) / np.pi)
                        * fderiv
                    )

        elif self.mesh.dim == 3:
            g3 = np.zeros((self.mesh.nC, self.npts * 2))
            if self.normal == "X":
                # Here we use perturbation to compute sensitivity
                for i in range(self.npts * 2):
                    ctemp = c[i]
                    ind = np.argmin(abs(self.mesh.vectorCCy - ctemp))
                    ca = c.copy()
                    cb = c.copy()
                    dy = self.mesh.hy[ind] * 1.5
                    ca[i] = ctemp + dy
                    cb[i] = ctemp - dy

                    # treat bottom boundary
                    if i < self.npts:
                        splba = UnivariateSpline(
                            self.pts, ca[: self.npts], k=self.order, s=0
                        )
                        splbb = UnivariateSpline(
                            self.pts, cb[: self.npts], k=self.order, s=0
                        )
                        flinesa = (
                            (self.spl["splt"](Y) - splba(Y)) * (Z - zb) / (zt - zb)
                            + splba(Y)
                            - X
                        )
                        flinesb = (
                            (self.spl["splt"](Y) - splbb(Y)) * (Z - zb) / (zt - zb)
                            + splbb(Y)
                            - X
                        )

                    # treat top boundary
                    else:
                        splta = UnivariateSpline(
                            self.pts, ca[self.npts :], k=self.order, s=0
                        )
                        spltb = UnivariateSpline(
                            self.pts, ca[self.npts :], k=self.order, s=0
                        )
                        flinesa = (
                            (self.spl["splt"](Y) - splta(Y)) * (Z - zb) / (zt - zb)
                            + splta(Y)
                            - X
                        )
                        flinesb = (
                            (self.spl["splt"](Y) - spltb(Y)) * (Z - zb) / (zt - zb)
                            + spltb(Y)
                            - X
                        )
                    fderiv = (flinesa - flinesb) / (2 * dy)
                    g3[:, i] = (
                        sdiag(alpha * (sig2 - sig1) / (1.0 + (alpha * f) ** 2) / np.pi)
                        * fderiv
                    )
        else:
            raise (Exception("Not Implemented for Y and Z, your turn :)"))

        if v is not None:
            return sp.csr_matrix(np.c_[g1, g2, g3]) * v
        return sp.csr_matrix(np.c_[g1, g2, g3])


class BaseParametric(IdentityMap):

    slopeFact = 1  # will be scaled by the mesh.
    slope = None
    indActive = None

    def __init__(self, mesh, **kwargs):
        super(BaseParametric, self).__init__(mesh, **kwargs)

        if self.slope is None:
            self.slope = self.slopeFact / np.hstack(self.mesh.h).min()

    @property
    def x(self):
        if getattr(self, "_x", None) is None:
            if self.mesh.dim == 1:
                self._x = [
                    self.mesh.gridCC
                    if self.indActive is None
                    else self.mesh.gridCC[self.indActive]
                ][0]
            else:
                self._x = [
                    self.mesh.gridCC[:, 0]
                    if self.indActive is None
                    else self.mesh.gridCC[self.indActive, 0]
                ][0]
        return self._x

    @property
    def y(self):
        if getattr(self, "_y", None) is None:
            if self.mesh.dim > 1:
                self._y = [
                    self.mesh.gridCC[:, 1]
                    if self.indActive is None
                    else self.mesh.gridCC[self.indActive, 1]
                ][0]
            else:
                self._y = None
        return self._y

    @property
    def z(self):
        if getattr(self, "_z", None) is None:
            if self.mesh.dim > 2:
                self._z = [
                    self.mesh.gridCC[:, 2]
                    if self.indActive is None
                    else self.mesh.gridCC[self.indActive, 2]
                ][0]
            else:
                self._z = None
        return self._z

    def _atanfct(self, val, slope):
        return np.arctan(slope * val) / np.pi + 0.5

    def _atanfctDeriv(self, val, slope):
        # d/dx(atan(x)) = 1/(1+x**2)
        x = slope * val
        dx = -slope
        return (1.0 / (1 + x ** 2)) / np.pi * dx


class ParametricLayer(BaseParametric):
    """
    Parametric Layer Space

    .. code:: python

        m = [
            val_background,
            val_layer,
            layer_center,
            layer_thickness
        ]

    **Required**

    :param discretize.base.BaseMesh mesh: SimPEG Mesh, 2D or 3D

    **Optional**

    :param float slopeFact: arctan slope factor - divided by the minimum h
                            spacing to give the slope of the arctan
                            functions
    :param float slope: slope of the arctan function
    :param numpy.ndarray indActive: bool vector with

    """

    def __init__(self, mesh, **kwargs):
        super(ParametricLayer, self).__init__(mesh, **kwargs)

    @property
    def nP(self):
        return 4

    @property
    def shape(self):
        if self.indActive is not None:
            return (sum(self.indActive), self.nP)
        return (self.mesh.nC, self.nP)

    def mDict(self, m):
        return {
            "val_background": m[0],
            "val_layer": m[1],
            "layer_center": m[2],
            "layer_thickness": m[3],
        }

    def _atanLayer(self, mDict):
        if self.mesh.dim == 2:
            z = self.y
        elif self.mesh.dim == 3:
            z = self.z

        layer_bottom = mDict["layer_center"] - mDict["layer_thickness"] / 2.0
        layer_top = mDict["layer_center"] + mDict["layer_thickness"] / 2.0

        return self._atanfct(z - layer_bottom, self.slope) * self._atanfct(
            z - layer_top, -self.slope
        )

    def _atanLayerDeriv_layer_center(self, mDict):
        if self.mesh.dim == 2:
            z = self.y
        elif self.mesh.dim == 3:
            z = self.z

        layer_bottom = mDict["layer_center"] - mDict["layer_thickness"] / 2.0
        layer_top = mDict["layer_center"] + mDict["layer_thickness"] / 2.0

        return self._atanfctDeriv(z - layer_bottom, self.slope) * self._atanfct(
            z - layer_top, -self.slope
        ) + self._atanfct(z - layer_bottom, self.slope) * self._atanfctDeriv(
            z - layer_top, -self.slope
        )

    def _atanLayerDeriv_layer_thickness(self, mDict):
        if self.mesh.dim == 2:
            z = self.y
        elif self.mesh.dim == 3:
            z = self.z

        layer_bottom = mDict["layer_center"] - mDict["layer_thickness"] / 2.0
        layer_top = mDict["layer_center"] + mDict["layer_thickness"] / 2.0

        return -0.5 * self._atanfctDeriv(z - layer_bottom, self.slope) * self._atanfct(
            z - layer_top, -self.slope
        ) + 0.5 * self._atanfct(z - layer_bottom, self.slope) * self._atanfctDeriv(
            z - layer_top, -self.slope
        )

    def layer_cont(self, mDict):
        return mDict["val_background"] + (
            mDict["val_layer"] - mDict["val_background"]
        ) * self._atanLayer(mDict)

    def _transform(self, m):
        mDict = self.mDict(m)
        return self.layer_cont(mDict)

    def _deriv_val_background(self, mDict):
        return np.ones_like(self.x) - self._atanLayer(mDict)

    def _deriv_val_layer(self, mDict):
        return self._atanLayer(mDict)

    def _deriv_layer_center(self, mDict):
        return (
            mDict["val_layer"] - mDict["val_background"]
        ) * self._atanLayerDeriv_layer_center(mDict)

    def _deriv_layer_thickness(self, mDict):
        return (
            mDict["val_layer"] - mDict["val_background"]
        ) * self._atanLayerDeriv_layer_thickness(mDict)

    def deriv(self, m):

        mDict = self.mDict(m)

        return sp.csr_matrix(
            np.vstack(
                [
                    self._deriv_val_background(mDict),
                    self._deriv_val_layer(mDict),
                    self._deriv_layer_center(mDict),
                    self._deriv_layer_thickness(mDict),
                ]
            ).T
        )


class ParametricBlock(BaseParametric):
    """
    Parametric Block in a Homogeneous Space

    For 1D:

    .. code:: python

        m = [
            val_background,
            val_block,
            block_x0,
            block_dx,
        ]

    For 2D:

    .. code:: python

        m = [
            val_background,
            val_block,
            block_x0,
            block_dx,
            block_y0,
            block_dy
        ]

    For 3D:

    .. code:: python

        m = [
            val_background,
            val_block,
            block_x0,
            block_dx,
            block_y0,
            block_dy
            block_z0,
            block_dz
        ]

    **Required**

    :param discretize.base.BaseMesh mesh: SimPEG Mesh, 2D or 3D

    **Optional**

    :param float slopeFact: arctan slope factor - divided by the minimum h
                            spacing to give the slope of the arctan
                            functions
    :param float slope: slope of the arctan function
    :param numpy.ndarray indActive: bool vector with active indices

    """

    epsilon = properties.Float(
        "epsilon value used in the ekblom representation of the block", default=1e-6
    )

    p = properties.Float(
        "p-value used in the ekblom representation of the block", default=10
    )

    def __init__(self, mesh, **kwargs):
        super(ParametricBlock, self).__init__(mesh, **kwargs)

    @property
    def nP(self):
        if self.mesh.dim == 1:
            return 4
        if self.mesh.dim == 2:
            return 6
        elif self.mesh.dim == 3:
            return 8

    @property
    def shape(self):
        if self.indActive is not None:
            return (sum(self.indActive), self.nP)
        return (self.mesh.nC, self.nP)

    def _mDict1d(self, m):
        return {
            "val_background": m[0],
            "val_block": m[1],
            "x0": m[2],
            "dx": m[3],
        }

    def _mDict2d(self, m):
        mDict = self._mDict1d(m)
        mDict.update(
            {
                # 'theta_x': m[4],
                "y0": m[4],
                "dy": m[5],
                # 'theta_y': m[7]
            }
        )
        return mDict

    def _mDict3d(self, m):
        mDict = self._mDict2d(m)
        mDict.update(
            {
                "z0": m[6],
                "dz": m[7],
                # 'theta_z': m[10]
            }
        )
        return mDict

    def mDict(self, m):
        return getattr(self, "_mDict{}d".format(self.mesh.dim))(m)

    def _ekblom(self, val):
        return (val ** 2 + self.epsilon ** 2) ** (self.p / 2.0)

    def _ekblomDeriv(self, val):
        return (
            (self.p / 2)
            * (val ** 2 + self.epsilon ** 2) ** ((self.p / 2) - 1)
            * 2
            * val
        )

    # def _rotation(self, mDict):
    #     if self.mesh.dim == 2:

    #     elif self.mesh.dim == 3:

    def _block1D(self, mDict):
        return 1 - (self._ekblom((self.x - mDict["x0"]) / (0.5 * mDict["dx"])))

    def _block2D(self, mDict):
        return 1 - (
            self._ekblom((self.x - mDict["x0"]) / (0.5 * mDict["dx"]))
            + self._ekblom((self.y - mDict["y0"]) / (0.5 * mDict["dy"]))
        )

    def _block3D(self, mDict):
        return 1 - (
            self._ekblom((self.x - mDict["x0"]) / (0.5 * mDict["dx"]))
            + self._ekblom((self.y - mDict["y0"]) / (0.5 * mDict["dy"]))
            + self._ekblom((self.z - mDict["z0"]) / (0.5 * mDict["dz"]))
        )

    def _transform(self, m):
        mDict = self.mDict(m)
        return mDict["val_background"] + (
            mDict["val_block"] - mDict["val_background"]
        ) * self._atanfct(
            getattr(self, "_block{}D".format(self.mesh.dim))(mDict), slope=self.slope
        )

    def _deriv_val_background(self, mDict):
        return 1 - self._atanfct(
            getattr(self, "_block{}D".format(self.mesh.dim))(mDict), slope=self.slope
        )

    def _deriv_val_block(self, mDict):
        return self._atanfct(
            getattr(self, "_block{}D".format(self.mesh.dim))(mDict), slope=self.slope
        )

    def _deriv_center_block(self, mDict, orientation):
        x = getattr(self, orientation)
        x0 = mDict["{}0".format(orientation)]
        dx = mDict["d{}".format(orientation)]
        return (mDict["val_block"] - mDict["val_background"]) * (
            self._atanfctDeriv(
                getattr(self, "_block{}D".format(self.mesh.dim))(mDict),
                slope=self.slope,
            )
            * (self._ekblomDeriv((x - x0) / (0.5 * dx)))
            / -(0.5 * dx)
        )

    def _deriv_width_block(self, mDict, orientation):
        x = getattr(self, orientation)
        x0 = mDict["{}0".format(orientation)]
        dx = mDict["d{}".format(orientation)]
        return (mDict["val_block"] - mDict["val_background"]) * (
            self._atanfctDeriv(
                getattr(self, "_block{}D".format(self.mesh.dim))(mDict),
                slope=self.slope,
            )
            * (self._ekblomDeriv((x - x0) / (0.5 * dx)) * (-(x - x0) / (0.5 * dx ** 2)))
        )

    def _deriv1D(self, mDict):
        return np.vstack(
            [
                self._deriv_val_background(mDict),
                self._deriv_val_block(mDict),
                self._deriv_center_block(mDict, "x"),
                self._deriv_width_block(mDict, "x"),
            ]
        ).T

    def _deriv2D(self, mDict):
        return np.vstack(
            [
                self._deriv_val_background(mDict),
                self._deriv_val_block(mDict),
                self._deriv_center_block(mDict, "x"),
                self._deriv_width_block(mDict, "x"),
                self._deriv_center_block(mDict, "y"),
                self._deriv_width_block(mDict, "y"),
            ]
        ).T

    def _deriv3D(self, mDict):
        return np.vstack(
            [
                self._deriv_val_background(mDict),
                self._deriv_val_block(mDict),
                self._deriv_center_block(mDict, "x"),
                self._deriv_width_block(mDict, "x"),
                self._deriv_center_block(mDict, "y"),
                self._deriv_width_block(mDict, "y"),
                self._deriv_center_block(mDict, "z"),
                self._deriv_width_block(mDict, "z"),
            ]
        ).T

    def deriv(self, m):
        return sp.csr_matrix(
            getattr(self, "_deriv{}D".format(self.mesh.dim))(self.mDict(m))
        )


class ParametricEllipsoid(ParametricBlock):

    # """
    #     Parametric Ellipsoid in a Homogeneous Space

    #     **Required**

    #     :param discretize.base.BaseMesh mesh: SimPEG Mesh, 2D or 3D

    #     **Optional**

    #     :param float slopeFact: arctan slope factor - divided by the minimum h
    #                             spacing to give the slope of the arctan
    #                             functions
    #     :param float slope: slope of the arctan function
    #     :param numpy.ndarray indActive: bool vector with active indices

    # """

    def __init__(self, mesh, **kwargs):
        super(ParametricEllipsoid, self).__init__(mesh, p=2, **kwargs)


class ParametricCasingAndLayer(ParametricLayer):
    """
    Parametric layered space with casing.

    .. code:: python

        m = [val_background,
             val_layer,
             val_casing,
             val_insideCasing,
             layer_center,
             layer_thickness,
             casing_radius,
             casing_thickness,
             casing_bottom,
             casing_top
        ]

    """

    def __init__(self, mesh, **kwargs):

        assert (
            mesh._meshType == "CYL"
        ), "Parametric Casing in a layer map only works for a cyl mesh."

        super(ParametricCasingAndLayer, self).__init__(mesh, **kwargs)

    @property
    def nP(self):
        return 10

    @property
    def shape(self):
        if self.indActive is not None:
            return (sum(self.indActive), self.nP)
        return (self.mesh.nC, self.nP)

    def mDict(self, m):
        # m = [val_background, val_layer, val_casing, val_insideCasing,
        #      layer_center, layer_thickness, casing_radius, casing_thickness,
        #      casing_bottom, casing_top]

        return {
            "val_background": m[0],
            "val_layer": m[1],
            "val_casing": m[2],
            "val_insideCasing": m[3],
            "layer_center": m[4],
            "layer_thickness": m[5],
            "casing_radius": m[6],
            "casing_thickness": m[7],
            "casing_bottom": m[8],
            "casing_top": m[9],
        }

    def casing_a(self, mDict):
        return mDict["casing_radius"] - 0.5 * mDict["casing_thickness"]

    def casing_b(self, mDict):
        return mDict["casing_radius"] + 0.5 * mDict["casing_thickness"]

    def _atanCasingLength(self, mDict):
        return self._atanfct(self.z - mDict["casing_top"], -self.slope) * self._atanfct(
            self.z - mDict["casing_bottom"], self.slope
        )

    def _atanCasingLengthDeriv_casing_top(self, mDict):
        return self._atanfctDeriv(
            self.z - mDict["casing_top"], -self.slope
        ) * self._atanfct(self.z - mDict["casing_bottom"], self.slope)

    def _atanCasingLengthDeriv_casing_bottom(self, mDict):
        return self._atanfct(
            self.z - mDict["casing_top"], -self.slope
        ) * self._atanfctDeriv(self.z - mDict["casing_bottom"], self.slope)

    def _atanInsideCasing(self, mDict):
        return self._atanCasingLength(mDict) * self._atanfct(
            self.x - self.casing_a(mDict), -self.slope
        )

    def _atanInsideCasingDeriv_casing_radius(self, mDict):
        return self._atanCasingLength(mDict) * self._atanfctDeriv(
            self.x - self.casing_a(mDict), -self.slope
        )

    def _atanInsideCasingDeriv_casing_thickness(self, mDict):
        return (
            self._atanCasingLength(mDict)
            * -0.5
            * self._atanfctDeriv(self.x - self.casing_a(mDict), -self.slope)
        )

    def _atanInsideCasingDeriv_casing_top(self, mDict):
        return self._atanCasingLengthDeriv_casing_top(mDict) * self._atanfct(
            self.x - self.casing_a(mDict), -self.slope
        )

    def _atanInsideCasingDeriv_casing_bottom(self, mDict):
        return self._atanCasingLengthDeriv_casing_bottom(mDict) * self._atanfct(
            self.x - self.casing_a(mDict), -self.slope
        )

    def _atanCasing(self, mDict):
        return (
            self._atanCasingLength(mDict)
            * self._atanfct(self.x - self.casing_a(mDict), self.slope)
            * self._atanfct(self.x - self.casing_b(mDict), -self.slope)
        )

    def _atanCasingDeriv_casing_radius(self, mDict):
        return self._atanCasingLength(mDict) * (
            self._atanfctDeriv(self.x - self.casing_a(mDict), self.slope)
            * self._atanfct(self.x - self.casing_b(mDict), -self.slope)
            + self._atanfct(self.x - self.casing_a(mDict), self.slope)
            * self._atanfctDeriv(self.x - self.casing_b(mDict), -self.slope)
        )

    def _atanCasingDeriv_casing_thickness(self, mDict):
        return self._atanCasingLength(mDict) * (
            -0.5
            * self._atanfctDeriv(self.x - self.casing_a(mDict), self.slope)
            * self._atanfct(self.x - self.casing_b(mDict), -self.slope)
            + self._atanfct(self.x - self.casing_a(mDict), self.slope)
            * 0.5
            * self._atanfctDeriv(self.x - self.casing_b(mDict), -self.slope)
        )

    def _atanCasingDeriv_casing_bottom(self, mDict):
        return (
            self._atanCasingLengthDeriv_casing_bottom(mDict)
            * self._atanfct(self.x - self.casing_a(mDict), self.slope)
            * self._atanfct(self.x - self.casing_b(mDict), -self.slope)
        )

    def _atanCasingDeriv_casing_top(self, mDict):
        return (
            self._atanCasingLengthDeriv_casing_top(mDict)
            * self._atanfct(self.x - self.casing_a(mDict), self.slope)
            * self._atanfct(self.x - self.casing_b(mDict), -self.slope)
        )

    def layer_cont(self, mDict):
        # contribution from the layered background
        return mDict["val_background"] + (
            mDict["val_layer"] - mDict["val_background"]
        ) * self._atanLayer(mDict)

    def _transform(self, m):

        mDict = self.mDict(m)

        # assemble the model
        layer = self.layer_cont(mDict)
        casing = (mDict["val_casing"] - layer) * self._atanCasing(mDict)
        insideCasing = (mDict["val_insideCasing"] - layer) * self._atanInsideCasing(
            mDict
        )

        return layer + casing + insideCasing

    def _deriv_val_background(self, mDict):
        # contribution from the layered background
        d_layer_cont_dval_background = 1.0 - self._atanLayer(mDict)
        d_casing_cont_dval_background = (
            -1.0 * d_layer_cont_dval_background * self._atanCasing(mDict)
        )
        d_insideCasing_cont_dval_background = (
            -1.0 * d_layer_cont_dval_background * self._atanInsideCasing(mDict)
        )
        return (
            d_layer_cont_dval_background
            + d_casing_cont_dval_background
            + d_insideCasing_cont_dval_background
        )

    def _deriv_val_layer(self, mDict):
        d_layer_cont_dval_layer = self._atanLayer(mDict)
        d_casing_cont_dval_layer = (
            -1.0 * d_layer_cont_dval_layer * self._atanCasing(mDict)
        )
        d_insideCasing_cont_dval_layer = (
            -1.0 * d_layer_cont_dval_layer * self._atanInsideCasing(mDict)
        )
        return (
            d_layer_cont_dval_layer
            + d_casing_cont_dval_layer
            + d_insideCasing_cont_dval_layer
        )

    def _deriv_val_casing(self, mDict):
        d_layer_cont_dval_casing = 0.0
        d_casing_cont_dval_casing = self._atanCasing(mDict)
        d_insideCasing_cont_dval_casing = 0.0
        return (
            d_layer_cont_dval_casing
            + d_casing_cont_dval_casing
            + d_insideCasing_cont_dval_casing
        )

    def _deriv_val_insideCasing(self, mDict):
        d_layer_cont_dval_insideCasing = 0.0
        d_casing_cont_dval_insideCasing = 0.0
        d_insideCasing_cont_dval_insideCasing = self._atanInsideCasing(mDict)
        return (
            d_layer_cont_dval_insideCasing
            + d_casing_cont_dval_insideCasing
            + d_insideCasing_cont_dval_insideCasing
        )

    def _deriv_layer_center(self, mDict):
        d_layer_cont_dlayer_center = (
            mDict["val_layer"] - mDict["val_background"]
        ) * self._atanLayerDeriv_layer_center(mDict)
        d_casing_cont_dlayer_center = -d_layer_cont_dlayer_center * self._atanCasing(
            mDict
        )
        d_insideCasing_cont_dlayer_center = (
            -d_layer_cont_dlayer_center * self._atanInsideCasing(mDict)
        )
        return (
            d_layer_cont_dlayer_center
            + d_casing_cont_dlayer_center
            + d_insideCasing_cont_dlayer_center
        )

    def _deriv_layer_thickness(self, mDict):
        d_layer_cont_dlayer_thickness = (
            mDict["val_layer"] - mDict["val_background"]
        ) * self._atanLayerDeriv_layer_thickness(mDict)
        d_casing_cont_dlayer_thickness = (
            -d_layer_cont_dlayer_thickness * self._atanCasing(mDict)
        )
        d_insideCasing_cont_dlayer_thickness = (
            -d_layer_cont_dlayer_thickness * self._atanInsideCasing(mDict)
        )
        return (
            d_layer_cont_dlayer_thickness
            + d_casing_cont_dlayer_thickness
            + d_insideCasing_cont_dlayer_thickness
        )

    def _deriv_casing_radius(self, mDict):
        layer = self.layer_cont(mDict)
        d_layer_cont_dcasing_radius = 0.0
        d_casing_cont_dcasing_radius = (
            mDict["val_casing"] - layer
        ) * self._atanCasingDeriv_casing_radius(mDict)
        d_insideCasing_cont_dcasing_radius = (
            mDict["val_insideCasing"] - layer
        ) * self._atanInsideCasingDeriv_casing_radius(mDict)
        return (
            d_layer_cont_dcasing_radius
            + d_casing_cont_dcasing_radius
            + d_insideCasing_cont_dcasing_radius
        )

    def _deriv_casing_thickness(self, mDict):
        d_layer_cont_dcasing_thickness = 0.0
        d_casing_cont_dcasing_thickness = (
            mDict["val_casing"] - self.layer_cont(mDict)
        ) * self._atanCasingDeriv_casing_thickness(mDict)
        d_insideCasing_cont_dcasing_thickness = (
            mDict["val_insideCasing"] - self.layer_cont(mDict)
        ) * self._atanInsideCasingDeriv_casing_thickness(mDict)
        return (
            d_layer_cont_dcasing_thickness
            + d_casing_cont_dcasing_thickness
            + d_insideCasing_cont_dcasing_thickness
        )

    def _deriv_casing_bottom(self, mDict):
        d_layer_cont_dcasing_bottom = 0.0
        d_casing_cont_dcasing_bottom = (
            mDict["val_casing"] - self.layer_cont(mDict)
        ) * self._atanCasingDeriv_casing_bottom(mDict)
        d_insideCasing_cont_dcasing_bottom = (
            mDict["val_insideCasing"] - self.layer_cont(mDict)
        ) * self._atanInsideCasingDeriv_casing_bottom(mDict)
        return (
            d_layer_cont_dcasing_bottom
            + d_casing_cont_dcasing_bottom
            + d_insideCasing_cont_dcasing_bottom
        )

    def _deriv_casing_top(self, mDict):
        d_layer_cont_dcasing_top = 0.0
        d_casing_cont_dcasing_top = (
            mDict["val_casing"] - self.layer_cont(mDict)
        ) * self._atanCasingDeriv_casing_top(mDict)
        d_insideCasing_cont_dcasing_top = (
            mDict["val_insideCasing"] - self.layer_cont(mDict)
        ) * self._atanInsideCasingDeriv_casing_top(mDict)
        return (
            d_layer_cont_dcasing_top
            + d_casing_cont_dcasing_top
            + d_insideCasing_cont_dcasing_top
        )

    def deriv(self, m):

        mDict = self.mDict(m)

        return sp.csr_matrix(
            np.vstack(
                [
                    self._deriv_val_background(mDict),
                    self._deriv_val_layer(mDict),
                    self._deriv_val_casing(mDict),
                    self._deriv_val_insideCasing(mDict),
                    self._deriv_layer_center(mDict),
                    self._deriv_layer_thickness(mDict),
                    self._deriv_casing_radius(mDict),
                    self._deriv_casing_thickness(mDict),
                    self._deriv_casing_bottom(mDict),
                    self._deriv_casing_top(mDict),
                ]
            ).T
        )


class ParametricBlockInLayer(ParametricLayer):
    """
    Parametric Block in a Layered Space

    For 2D:

    .. code:: python

        m = [val_background,
             val_layer,
             val_block,
             layer_center,
             layer_thickness,
             block_x0,
             block_dx
        ]

    For 3D:

    .. code:: python

        m = [val_background,
             val_layer,
             val_block,
             layer_center,
             layer_thickness,
             block_x0,
             block_y0,
             block_dx,
             block_dy
        ]

    **Required**

    :param discretize.base.BaseMesh mesh: SimPEG Mesh, 2D or 3D

    **Optional**

    :param float slopeFact: arctan slope factor - divided by the minimum h
                            spacing to give the slope of the arctan
                            functions
    :param float slope: slope of the arctan function
    :param numpy.ndarray indActive: bool vector with

    """

    def __init__(self, mesh, **kwargs):

        super(ParametricBlockInLayer, self).__init__(mesh, **kwargs)

    @property
    def nP(self):
        if self.mesh.dim == 2:
            return 7
        elif self.mesh.dim == 3:
            return 9

    @property
    def shape(self):
        if self.indActive is not None:
            return (sum(self.indActive), self.nP)
        return (self.mesh.nC, self.nP)

    def _mDict2d(self, m):
        return {
            "val_background": m[0],
            "val_layer": m[1],
            "val_block": m[2],
            "layer_center": m[3],
            "layer_thickness": m[4],
            "x0": m[5],
            "dx": m[6],
        }

    def _mDict3d(self, m):
        return {
            "val_background": m[0],
            "val_layer": m[1],
            "val_block": m[2],
            "layer_center": m[3],
            "layer_thickness": m[4],
            "x0": m[5],
            "y0": m[6],
            "dx": m[7],
            "dy": m[8],
        }

    def mDict(self, m):
        if self.mesh.dim == 2:
            return self._mDict2d(m)
        elif self.mesh.dim == 3:
            return self._mDict3d(m)

    def xleft(self, mDict):
        return mDict["x0"] - 0.5 * mDict["dx"]

    def xright(self, mDict):
        return mDict["x0"] + 0.5 * mDict["dx"]

    def yleft(self, mDict):
        return mDict["y0"] - 0.5 * mDict["dy"]

    def yright(self, mDict):
        return mDict["y0"] + 0.5 * mDict["dy"]

    def _atanBlock2d(self, mDict):
        return (
            self._atanLayer(mDict)
            * self._atanfct(self.x - self.xleft(mDict), self.slope)
            * self._atanfct(self.x - self.xright(mDict), -self.slope)
        )

    def _atanBlock2dDeriv_layer_center(self, mDict):
        return (
            self._atanLayerDeriv_layer_center(mDict)
            * self._atanfct(self.x - self.xleft(mDict), self.slope)
            * self._atanfct(self.x - self.xright(mDict), -self.slope)
        )

    def _atanBlock2dDeriv_layer_thickness(self, mDict):
        return (
            self._atanLayerDeriv_layer_thickness(mDict)
            * self._atanfct(self.x - self.xleft(mDict), self.slope)
            * self._atanfct(self.x - self.xright(mDict), -self.slope)
        )

    def _atanBlock2dDeriv_x0(self, mDict):
        return self._atanLayer(mDict) * (
            (
                self._atanfctDeriv(self.x - self.xleft(mDict), self.slope)
                * self._atanfct(self.x - self.xright(mDict), -self.slope)
            )
            + (
                self._atanfct(self.x - self.xleft(mDict), self.slope)
                * self._atanfctDeriv(self.x - self.xright(mDict), -self.slope)
            )
        )

    def _atanBlock2dDeriv_dx(self, mDict):
        return self._atanLayer(mDict) * (
            (
                self._atanfctDeriv(self.x - self.xleft(mDict), self.slope)
                * -0.5
                * self._atanfct(self.x - self.xright(mDict), -self.slope)
            )
            + (
                self._atanfct(self.x - self.xleft(mDict), self.slope)
                * 0.5
                * self._atanfctDeriv(self.x - self.xright(mDict), -self.slope)
            )
        )

    def _atanBlock3d(self, mDict):
        return (
            self._atanLayer(mDict)
            * self._atanfct(self.x - self.xleft(mDict), self.slope)
            * self._atanfct(self.x - self.xright(mDict), -self.slope)
            * self._atanfct(self.y - self.yleft(mDict), self.slope)
            * self._atanfct(self.y - self.yright(mDict), -self.slope)
        )

    def _atanBlock3dDeriv_layer_center(self, mDict):
        return (
            self._atanLayerDeriv_layer_center(mDict)
            * self._atanfct(self.x - self.xleft(mDict), self.slope)
            * self._atanfct(self.x - self.xright(mDict), -self.slope)
            * self._atanfct(self.y - self.yleft(mDict), self.slope)
            * self._atanfct(self.y - self.yright(mDict), -self.slope)
        )

    def _atanBlock3dDeriv_layer_thickness(self, mDict):
        return (
            self._atanLayerDeriv_layer_thickness(mDict)
            * self._atanfct(self.x - self.xleft(mDict), self.slope)
            * self._atanfct(self.x - self.xright(mDict), -self.slope)
            * self._atanfct(self.y - self.yleft(mDict), self.slope)
            * self._atanfct(self.y - self.yright(mDict), -self.slope)
        )

    def _atanBlock3dDeriv_x0(self, mDict):
        return self._atanLayer(mDict) * (
            (
                self._atanfctDeriv(self.x - self.xleft(mDict), self.slope)
                * self._atanfct(self.x - self.xright(mDict), -self.slope)
                * self._atanfct(self.y - self.yleft(mDict), self.slope)
                * self._atanfct(self.y - self.yright(mDict), -self.slope)
            )
            + (
                self._atanfct(self.x - self.xleft(mDict), self.slope)
                * self._atanfctDeriv(self.x - self.xright(mDict), -self.slope)
                * self._atanfct(self.y - self.yleft(mDict), self.slope)
                * self._atanfct(self.y - self.yright(mDict), -self.slope)
            )
        )

    def _atanBlock3dDeriv_y0(self, mDict):
        return self._atanLayer(mDict) * (
            (
                self._atanfct(self.x - self.xleft(mDict), self.slope)
                * self._atanfct(self.x - self.xright(mDict), -self.slope)
                * self._atanfctDeriv(self.y - self.yleft(mDict), self.slope)
                * self._atanfct(self.y - self.yright(mDict), -self.slope)
            )
            + (
                self._atanfct(self.x - self.xleft(mDict), self.slope)
                * self._atanfct(self.x - self.xright(mDict), -self.slope)
                * self._atanfct(self.y - self.yleft(mDict), self.slope)
                * self._atanfctDeriv(self.y - self.yright(mDict), -self.slope)
            )
        )

    def _atanBlock3dDeriv_dx(self, mDict):
        return self._atanLayer(mDict) * (
            (
                self._atanfctDeriv(self.x - self.xleft(mDict), self.slope)
                * -0.5
                * self._atanfct(self.x - self.xright(mDict), -self.slope)
                * self._atanfct(self.y - self.yleft(mDict), self.slope)
                * self._atanfct(self.y - self.yright(mDict), -self.slope)
            )
            + (
                self._atanfct(self.x - self.xleft(mDict), self.slope)
                * self._atanfctDeriv(self.x - self.xright(mDict), -self.slope)
                * 0.5
                * self._atanfct(self.y - self.yleft(mDict), self.slope)
                * self._atanfct(self.y - self.yright(mDict), -self.slope)
            )
        )

    def _atanBlock3dDeriv_dy(self, mDict):
        return self._atanLayer(mDict) * (
            (
                self._atanfct(self.x - self.xleft(mDict), self.slope)
                * self._atanfct(self.x - self.xright(mDict), -self.slope)
                * self._atanfctDeriv(self.y - self.yleft(mDict), self.slope)
                * -0.5
                * self._atanfct(self.y - self.yright(mDict), -self.slope)
            )
            + (
                self._atanfct(self.x - self.xleft(mDict), self.slope)
                * self._atanfct(self.x - self.xright(mDict), -self.slope)
                * self._atanfct(self.y - self.yleft(mDict), self.slope)
                * self._atanfctDeriv(self.y - self.yright(mDict), -self.slope)
                * 0.5
            )
        )

    def _transform2d(self, m):
        mDict = self.mDict(m)
        # assemble the model
        # contribution from the layered background
        layer_cont = mDict["val_background"] + (
            mDict["val_layer"] - mDict["val_background"]
        ) * self._atanLayer(mDict)

        # perturbation due to the blocks
        block_cont = (mDict["val_block"] - layer_cont) * self._atanBlock2d(mDict)

        return layer_cont + block_cont

    def _deriv2d_val_background(self, mDict):
        d_layer_dval_background = np.ones_like(self.x) - self._atanLayer(mDict)
        d_block_dval_background = (-d_layer_dval_background) * self._atanBlock2d(mDict)
        return d_layer_dval_background + d_block_dval_background

    def _deriv2d_val_layer(self, mDict):
        d_layer_dval_layer = self._atanLayer(mDict)
        d_block_dval_layer = (-d_layer_dval_layer) * self._atanBlock2d(mDict)
        return d_layer_dval_layer + d_block_dval_layer

    def _deriv2d_val_block(self, mDict):
        d_layer_dval_block = 0.0
        d_block_dval_block = (1.0 - d_layer_dval_block) * self._atanBlock2d(mDict)
        return d_layer_dval_block + d_block_dval_block

    def _deriv2d_layer_center(self, mDict):
        d_layer_dlayer_center = (
            mDict["val_layer"] - mDict["val_background"]
        ) * self._atanLayerDeriv_layer_center(mDict)
        d_block_dlayer_center = (
            mDict["val_block"] - self.layer_cont(mDict)
        ) * self._atanBlock2dDeriv_layer_center(
            mDict
        ) - d_layer_dlayer_center * self._atanBlock2d(
            mDict
        )
        return d_layer_dlayer_center + d_block_dlayer_center

    def _deriv2d_layer_thickness(self, mDict):
        d_layer_dlayer_thickness = (
            mDict["val_layer"] - mDict["val_background"]
        ) * self._atanLayerDeriv_layer_thickness(mDict)
        d_block_dlayer_thickness = (
            mDict["val_block"] - self.layer_cont(mDict)
        ) * self._atanBlock2dDeriv_layer_thickness(
            mDict
        ) - d_layer_dlayer_thickness * self._atanBlock2d(
            mDict
        )
        return d_layer_dlayer_thickness + d_block_dlayer_thickness

    def _deriv2d_x0(self, mDict):
        d_layer_dx0 = 0.0
        d_block_dx0 = (
            mDict["val_block"] - self.layer_cont(mDict)
        ) * self._atanBlock2dDeriv_x0(mDict)
        return d_layer_dx0 + d_block_dx0

    def _deriv2d_dx(self, mDict):
        d_layer_ddx = 0.0
        d_block_ddx = (
            mDict["val_block"] - self.layer_cont(mDict)
        ) * self._atanBlock2dDeriv_dx(mDict)
        return d_layer_ddx + d_block_ddx

    def _deriv2d(self, m):
        mDict = self.mDict(m)

        return np.vstack(
            [
                self._deriv2d_val_background(mDict),
                self._deriv2d_val_layer(mDict),
                self._deriv2d_val_block(mDict),
                self._deriv2d_layer_center(mDict),
                self._deriv2d_layer_thickness(mDict),
                self._deriv2d_x0(mDict),
                self._deriv2d_dx(mDict),
            ]
        ).T

    def _transform3d(self, m):
        # parse model
        mDict = self.mDict(m)

        # assemble the model
        # contribution from the layered background
        layer_cont = mDict["val_background"] + (
            mDict["val_layer"] - mDict["val_background"]
        ) * self._atanLayer(mDict)
        # perturbation due to the block
        block_cont = (mDict["val_block"] - layer_cont) * self._atanBlock3d(mDict)

        return layer_cont + block_cont

    def _deriv3d_val_background(self, mDict):
        d_layer_dval_background = np.ones_like(self.x) - self._atanLayer(mDict)
        d_block_dval_background = (-d_layer_dval_background) * self._atanBlock3d(mDict)
        return d_layer_dval_background + d_block_dval_background

    def _deriv3d_val_layer(self, mDict):
        d_layer_dval_layer = self._atanLayer(mDict)
        d_block_dval_layer = (-d_layer_dval_layer) * self._atanBlock3d(mDict)
        return d_layer_dval_layer + d_block_dval_layer

    def _deriv3d_val_block(self, mDict):
        d_layer_dval_block = 0.0
        d_block_dval_block = (1.0 - d_layer_dval_block) * self._atanBlock3d(mDict)
        return d_layer_dval_block + d_block_dval_block

    def _deriv3d_layer_center(self, mDict):
        d_layer_dlayer_center = (
            mDict["val_layer"] - mDict["val_background"]
        ) * self._atanLayerDeriv_layer_center(mDict)
        d_block_dlayer_center = (
            mDict["val_block"] - self.layer_cont(mDict)
        ) * self._atanBlock3dDeriv_layer_center(
            mDict
        ) - d_layer_dlayer_center * self._atanBlock3d(
            mDict
        )
        return d_layer_dlayer_center + d_block_dlayer_center

    def _deriv3d_layer_thickness(self, mDict):
        d_layer_dlayer_thickness = (
            mDict["val_layer"] - mDict["val_background"]
        ) * self._atanLayerDeriv_layer_thickness(mDict)
        d_block_dlayer_thickness = (
            mDict["val_block"] - self.layer_cont(mDict)
        ) * self._atanBlock3dDeriv_layer_thickness(
            mDict
        ) - d_layer_dlayer_thickness * self._atanBlock3d(
            mDict
        )
        return d_layer_dlayer_thickness + d_block_dlayer_thickness

    def _deriv3d_x0(self, mDict):
        d_layer_dx0 = 0.0
        d_block_dx0 = (
            mDict["val_block"] - self.layer_cont(mDict)
        ) * self._atanBlock3dDeriv_x0(mDict)
        return d_layer_dx0 + d_block_dx0

    def _deriv3d_y0(self, mDict):
        d_layer_dy0 = 0.0
        d_block_dy0 = (
            mDict["val_block"] - self.layer_cont(mDict)
        ) * self._atanBlock3dDeriv_y0(mDict)
        return d_layer_dy0 + d_block_dy0

    def _deriv3d_dx(self, mDict):
        d_layer_ddx = 0.0
        d_block_ddx = (
            mDict["val_block"] - self.layer_cont(mDict)
        ) * self._atanBlock3dDeriv_dx(mDict)
        return d_layer_ddx + d_block_ddx

    def _deriv3d_dy(self, mDict):
        d_layer_ddy = 0.0
        d_block_ddy = (
            mDict["val_block"] - self.layer_cont(mDict)
        ) * self._atanBlock3dDeriv_dy(mDict)
        return d_layer_ddy + d_block_ddy

    def _deriv3d(self, m):

        mDict = self.mDict(m)

        return np.vstack(
            [
                self._deriv3d_val_background(mDict),
                self._deriv3d_val_layer(mDict),
                self._deriv3d_val_block(mDict),
                self._deriv3d_layer_center(mDict),
                self._deriv3d_layer_thickness(mDict),
                self._deriv3d_x0(mDict),
                self._deriv3d_y0(mDict),
                self._deriv3d_dx(mDict),
                self._deriv3d_dy(mDict),
            ]
        ).T

    def _transform(self, m):

        if self.mesh.dim == 2:
            return self._transform2d(m)
        elif self.mesh.dim == 3:
            return self._transform3d(m)

    def deriv(self, m):

        if self.mesh.dim == 2:
            return sp.csr_matrix(self._deriv2d(m))
        elif self.mesh.dim == 3:
            return sp.csr_matrix(self._deriv3d(m))


class TileMap(IdentityMap):
    """
    Mapping for tiled inversion.

    Uses volume averaging to map a model defined on a global mesh to the
    local mesh. Everycell in the local mesh must also be in the global mesh.
    """

    tol = 1e-8  # Tolerance to avoid zero division
    components = 1  # Number of components in the model. =3 for vector model

    def __init__(self, global_mesh, global_active, local_mesh, **kwargs):
        """
        Parameters
        ----------
        global_mesh : discretize.TreeMesh
            Global TreeMesh defining the entire domain.
        global_active : bool, array of bool, or array of indices
            Defines the active cells in the global_mesh.
        local_mesh : discretize.TreeMesh
            Local TreeMesh for the simulation.
        """
        kwargs.pop("mesh", None)
        if global_mesh._meshType != "TREE":
            raise ValueError("global_mesh must be a TreeMesh")
        if local_mesh._meshType != "TREE":
            raise ValueError("local_mesh must be a TreeMesh")

        super(TileMap, self).__init__(**kwargs)
        self.global_mesh = global_mesh
        self.global_active = global_active
        self.local_mesh = local_mesh

        if not isinstance(self.global_active, bool):
            temp = np.zeros(self.global_mesh.nC, dtype="bool")
            temp[self.global_active] = True
            self.global_active = temp

        self.P

    @property
    def local_active(self):
        """
        This is the local_active of the global_active used in the global problem.
        """
        return getattr(self, "_local_active", None)

    @local_active.setter
    def local_active(self, local_active):

        if not isinstance(local_active, bool):
            temp = np.zeros(self.local_mesh.nC, dtype="bool")
            temp[local_active] = True
            local_active = temp

        self._local_active = local_active

    @property
    def P(self):
        """
        Set the projection matrix with partial volumes
        """
        if getattr(self, "_P", None) is None:

            in_local = self.local_mesh._get_containing_cell_indexes(
                self.global_mesh.gridCC
            )

            P = (
                sp.csr_matrix(
                    (self.global_mesh.vol, (in_local, np.arange(self.global_mesh.nC))),
                    shape=(self.local_mesh.nC, self.global_mesh.nC),
                )
                * speye(self.global_mesh.nC)[:, self.global_active]
            )

            self.local_active = mkvc(np.sum(P, axis=1) > 0)

            P = P[self.local_active, :]

            self._P = sp.block_diag(
                [
                    sdiag(1.0 / self.local_mesh.vol[self.local_active]) * P
                    for ii in range(self.components)
                ]
            )

        return self._P

    def _transform(self, m):
        return self.P * m

    @property
    def shape(self):
        """
        Shape of the matrix operation (number of indices x nP)
        """
        return self.P.shape

    def deriv(self, m, v=None):
        """
        :param numpy.ndarray m: model
        :rtype: scipy.sparse.csr_matrix
        :return: derivative of transformed model
        """
        if v is not None:
            return self.P * v
        return self.P


###############################################################################
#                                                                             #
#                       Maps for petrophsyics clusters                        #
#                                                                             #
###############################################################################


class PolynomialPetroClusterMap(IdentityMap):
    """
    Modeling polynomial relationships between physical properties

    """

    def __init__(
        self,
        coeffxx=np.r_[0.0, 1],
        coeffxy=np.zeros(1),
        coeffyx=np.zeros(1),
        coeffyy=np.r_[0.0, 1],
        mesh=None,
        nP=None,
        **kwargs
    ):

        self.coeffxx = coeffxx
        self.coeffxy = coeffxy
        self.coeffyx = coeffyx
        self.coeffyy = coeffyy
        self.polynomialxx = polynomial.Polynomial(self.coeffxx)
        self.polynomialxy = polynomial.Polynomial(self.coeffxy)
        self.polynomialyx = polynomial.Polynomial(self.coeffyx)
        self.polynomialyy = polynomial.Polynomial(self.coeffyy)
        self.polynomialxx_deriv = self.polynomialxx.deriv(m=1)
        self.polynomialxy_deriv = self.polynomialxy.deriv(m=1)
        self.polynomialyx_deriv = self.polynomialyx.deriv(m=1)
        self.polynomialyy_deriv = self.polynomialyy.deriv(m=1)

        super(PolynomialPetroClusterMap, self).__init__(mesh=mesh, nP=nP, **kwargs)

    def _transform(self, m):
        out = m.copy()
        out[:, 0] = self.polynomialxx(m[:, 0]) + self.polynomialxy(m[:, 1])
        out[:, 1] = self.polynomialyx(m[:, 0]) + self.polynomialyy(m[:, 1])
        return out

    def inverse(self, D):
        """
        :param numpy.array D: physical property
        :rtype: numpy.array
        :return: model

        The *transformInverse* changes the physical property into the
        model.

        .. math::

            m = \log{\sigma}

        """
        raise Exception("Not implemented")

    def _derivmatrix(self, m):
        return np.r_[
            [
                [
                    self.polynomialxx_deriv(m[:, 0])[0],
                    self.polynomialyx_deriv(m[:, 0])[0],
                ],
                [
                    self.polynomialxy_deriv(m[:, 1])[0],
                    self.polynomialyy_deriv(m[:, 1])[0],
                ],
            ]
        ]

    def deriv(self, m, v=None):
        """"""
        if v is None:
            out = self._derivmatrix(m.reshape(-1, 2))
            return out
        else:
            out = np.dot(self._derivmatrix(m.reshape(-1, 2)), v.reshape(2, -1))
            return out


###############################################################################
#                                                                             #
#                              Deprecated Maps                               #
#                                                                             #
###############################################################################


@deprecate_class(removal_version="0.16.0", error=True)
class FullMap(SurjectFull):
    pass


@deprecate_class(removal_version="0.16.0", error=True)
class Vertical1DMap(SurjectVertical1D):
    pass


@deprecate_class(removal_version="0.16.0", error=True)
class Map2Dto3D(Surject2Dto3D):
    pass


@deprecate_class(removal_version="0.16.0", error=True)
class ActiveCells(InjectActiveCells):
    pass


@deprecate_class(removal_version="0.16.0", error=True)
class CircleMap(ParametricCircleMap):
    pass


@deprecate_class(removal_version="0.16.0", error=True)
class PolyMap(ParametricPolyMap):
    pass


@deprecate_class(removal_version="0.16.0", error=True)
class SplineMap(ParametricSplineMap):
    pass
