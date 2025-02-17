import time
import sys
import numpy as np
from scipy.constants import mu_0
from ...utils.code_utils import deprecate_class

from ...utils import mkvc
from ..frequency_domain.simulation import BaseFDEMSimulation
from ..utils import omega
from .survey import Data
from .fields import Fields1DPrimarySecondary, Fields3DPrimarySecondary


class BaseNSEMSimulation(BaseFDEMSimulation):
    """
    Base class for all Natural source problems.
    """

    # fieldsPair = BaseNSEMFields

    # def __init__(self, mesh, **kwargs):
    #     super(BaseNSEMSimulation, self).__init__()
    #     BaseFDEMProblem.__init__(self, mesh, **kwargs)
    #     setKwargs(self, **kwargs)
    # # Set the default pairs of the problem
    # surveyPair = Survey
    # dataPair = Data

    # Notes:
    # Use the fields and devs methods from BaseFDEMProblem

    # NEED to clean up the Jvec and Jtvec to use Zero and Identities for None components.
    def Jvec(self, m, v, f=None):
        """
        Function to calculate the data sensitivities dD/dm times a vector.

        :param numpy.ndarray m: conductivity model (nP,)
        :param numpy.ndarray v: vector which we take sensitivity product with (nP,)
        :param SimPEG.electromagnetics.frequency_domain.fields.FieldsFDEM (optional) u: NSEM fields object, if not given it is calculated
        :rtype: numpy.ndarray
        :return: Jv (nData,) Data sensitivities wrt m
        """

        # Calculate the fields if not given as input
        if f is None:
            f = self.fields(m)
        # Set current model
        self.model = m
        # Initiate the Jv object
        Jv = Data(self.survey)

        # Loop all the frequenies
        for freq in self.survey.frequencies:
            # Get the system
            A = self.getA(freq)
            # Factor
            Ainv = self.solver(A, **self.solver_opts)

            for src in self.survey.get_sources_by_frequency(freq):
                # We need fDeriv_m = df/du*du/dm + df/dm
                # Construct du/dm, it requires a solve
                # NOTE: need to account for the 2 polarizations in the derivatives.
                u_src = f[
                    src, :
                ]  # u should be a vector by definition. Need to fix this...
                # dA_dm and dRHS_dm should be of size nE,2, so that we can multiply by Ainv.
                # The 2 columns are each of the polarizations.
                dA_dm_v = self.getADeriv(
                    freq, u_src, v
                )  # Size: nE,2 (u_px,u_py) in the columns.
                dRHS_dm_v = self.getRHSDeriv(
                    freq, v
                )  # Size: nE,2 (u_px,u_py) in the columns.
                # Calculate du/dm*v
                du_dm_v = Ainv * (-dA_dm_v + dRHS_dm_v)
                # Calculate the projection derivatives
                for rx in src.receiver_list:
                    # Calculate dP/du*du/dm*v
                    Jv[src, rx] = rx.evalDeriv(
                        src, self.mesh, f, mkvc(du_dm_v)
                    )  # wrt uPDeriv_u(mkvc(du_dm))
            Ainv.clean()
        # Return the vectorized sensitivities
        return mkvc(Jv)

    def Jtvec(self, m, v, f=None):
        """
        Function to calculate the transpose of the data sensitivities (dD/dm)^T times a vector.

        :param numpy.ndarray m: inversion model (nP,)
        :param numpy.ndarray v: vector which we take adjoint product with (nP,)
        :param SimPEG.electromagnetics.frequency_domain.fields.FieldsFDEM f (optional): NSEM fields object, if not given it is calculated
        :rtype: numpy.ndarray
        :return: Jtv (nP,) Data sensitivities wrt m
        """

        if f is None:
            f = self.fields(m)

        self.model = m

        # Ensure v is a data object.
        if not isinstance(v, Data):
            v = Data(self.survey, v)

        Jtv = np.zeros(m.size)

        for freq in self.survey.frequencies:
            AT = self.getA(freq).T

            ATinv = self.solver(AT, **self.solver_opts)

            for src in self.survey.get_sources_by_frequency(freq):
                # u_src needs to have both polarizations
                u_src = f[src, :]

                for rx in src.receiver_list:
                    # Get the adjoint evalDeriv
                    # PTv needs to be nE,2
                    PTv = rx.evalDeriv(
                        src, self.mesh, f, mkvc(v[src, rx]), adjoint=True
                    )  # wrt f, need possibility wrt m
                    # Get the
                    dA_duIT = mkvc(ATinv * PTv)  # Force (nU,) shape
                    dA_dmT = self.getADeriv(freq, u_src, dA_duIT, adjoint=True)
                    dRHS_dmT = self.getRHSDeriv(freq, dA_duIT, adjoint=True)
                    # Make du_dmT
                    du_dmT = -dA_dmT + dRHS_dmT
                    # Select the correct component
                    # du_dmT needs to be of size (nP,) number of model parameters
                    real_or_imag = rx.component
                    if real_or_imag == "real":
                        Jtv += np.array(du_dmT, dtype=complex).real
                    elif real_or_imag == "imag":
                        Jtv += -np.array(du_dmT, dtype=complex).real
                    else:
                        raise Exception("Must be real or imag")
            # Clean the factorization, clear memory.
            ATinv.clean()
        return Jtv


###################################
# 1D problems
###################################


class Simulation1DPrimarySecondary(BaseNSEMSimulation):
    """
    A NSEM problem soving a e formulation and primary/secondary fields decomposion.

    By eliminating the magnetic flux density using

        .. math ::

            \mathbf{b} = \\frac{1}{i \omega}\\left(-\mathbf{C} \mathbf{e} \\right)


    we can write Maxwell's equations as a second order system in \\\(\\\mathbf{e}\\\) only:

    .. math ::
        \\left[ \mathbf{C}^{\\top} \mathbf{M_{\mu^{-1}}^e } \mathbf{C} + i \omega \mathbf{M_{\sigma}^f} \\right] \mathbf{e}_{s} = i \omega \mathbf{M_{\sigma_{s}}^f } \mathbf{e}_{p}

    which we solve for :math:`\\mathbf{e_s}`. The total field :math:`\mathbf{e} = \mathbf{e_p} + \mathbf{e_s}`.

    The primary field is estimated from a background model (commonly half space ).


    """

    # From FDEMproblem: Used to project the fields. Currently not used for NSEMproblem.
    _solutionType = "e_1dSolution"
    _formulation = "EF"
    fieldsPair = Fields1DPrimarySecondary

    # Initiate properties
    _sigmaPrimary = None

    def __init__(self, mesh, **kwargs):
        BaseNSEMSimulation.__init__(self, mesh, **kwargs)
        # self._sigmaPrimary = sigmaPrimary

    @property
    def MeMui(self):
        """
        Edge inner product matrix
        """
        if getattr(self, "_MeMui", None) is None:
            self._MeMui = self.mesh.getEdgeInnerProduct(1.0 / mu_0)
        return self._MeMui

    @property
    def MfSigma(self):
        """
        Edge inner product matrix
        """
        # if getattr(self, '_MfSigma', None) is None:
        self._MfSigma = self.mesh.getFaceInnerProduct(self.sigma)
        return self._MfSigma

    def MfSigmaDeriv(self, u):
        """
        Edge inner product matrix
        """
        # if getattr(self, '_MfSigmaDeriv', None) is None:
        self._MfSigmaDeriv = (
            self.mesh.getFaceInnerProductDeriv(self.sigma)(u) * self.sigmaDeriv
        )
        return self._MfSigmaDeriv

    @property
    def sigmaPrimary(self):
        """
        A background model, use for the calculation of the primary fields.

        """
        return self._sigmaPrimary

    @sigmaPrimary.setter
    def sigmaPrimary(self, val):
        # Note: TODO add logic for val, make sure it is the correct size.
        self._sigmaPrimary = val

    def getA(self, freq):
        """
        Function to get the A matrix.

        :param float freq: Frequency
        :rtype: scipy.sparse.csr_matrix
        :return: A
        """

        # Note: need to use the code above since in the 1D problem I want
        # e to live on Faces(nodes) and h on edges(cells). Might need to rethink this
        # Possible that _fieldType and _eqLocs can fix this
        MeMui = self.MeMui
        MfSigma = self.MfSigma
        C = self.mesh.nodalGrad
        # Make A
        A = C.T * MeMui * C + 1j * omega(freq) * MfSigma
        # Either return full or only the inner part of A
        return A

    def getADeriv(self, freq, u, v, adjoint=False):
        """
        The derivative of A wrt sigma
        """

        u_src = u["e_1dSolution"]
        dMfSigma_dm = self.MfSigmaDeriv(u_src)
        if adjoint:
            return 1j * omega(freq) * mkvc(dMfSigma_dm.T * v,)
        # Note: output has to be nN/nF, not nC/nE.
        # v should be nC
        return 1j * omega(freq) * mkvc(dMfSigma_dm * v,)

    def getRHS(self, freq):
        """
        Function to return the right hand side for the system.

        :param float freq: Frequency
        :rtype: numpy.ndarray
        :return: RHS for 1 polarizations, primary fields (nF, 1)
        """

        # Get sources for the frequncy(polarizations)
        Src = self.survey.get_sources_by_frequency(freq)[0]
        # Only select the yx polarization
        S_e = mkvc(Src.S_e(self)[:, 1], 2)
        return -1j * omega(freq) * S_e

    def getRHSDeriv(self, freq, v, adjoint=False):
        """
        The derivative of the RHS wrt sigma
        """

        Src = self.survey.get_sources_by_frequency(freq)[0]

        S_eDeriv = mkvc(Src.S_eDeriv_m(self, v, adjoint),)
        return -1j * omega(freq) * S_eDeriv

    def fields(self, m=None):
        """
        Function to calculate all the fields for the model m.

        :param numpy.ndarray m: Conductivity model (nC,)
        :rtype: SimPEG.electromagnetics.natural_source.fields.Fields1DPrimarySecondary
        :return: NSEM fields object containing the solution
        """
        # Set the current model
        if m is not None:
            self.model = m
        # Make the fields object
        F = self.fieldsPair(self)
        # Loop over the frequencies
        for freq in self.survey.frequencies:
            if self.verbose:
                startTime = time.time()
                print("Starting work for {:.3e}".format(freq))
                sys.stdout.flush()
            A = self.getA(freq)
            rhs = self.getRHS(freq)
            Ainv = self.solver(A, **self.solver_opts)
            e_s = Ainv * rhs

            # Store the fields
            Src = self.survey.get_sources_by_frequency(freq)[0]
            # NOTE: only store the e_solution(secondary), all other components calculated in the fields object
            F[Src, "e_1dSolution"] = e_s

            if self.verbose:
                print("Ran for {:f} seconds".format(time.time() - startTime))
                sys.stdout.flush()
        return F


###################################
# 3D problems
###################################
class Simulation3DPrimarySecondary(BaseNSEMSimulation):
    """
    A NSEM problem solving a e formulation and a primary/secondary fields decompostion.

    By eliminating the magnetic flux density using

        .. math ::

            \mathbf{b} = \\frac{1}{i \omega}\\left(-\mathbf{C} \mathbf{e} \\right)


    we can write Maxwell's equations as a second order system in :math:`\mathbf{e}` only:

    .. math ::

        \\left[\mathbf{C}^{\\top} \mathbf{M_{\mu^{-1}}^f} \mathbf{C} + i \omega \mathbf{M_{\sigma}^e} \\right] \mathbf{e}_{s} = i \omega \mathbf{M_{\sigma_{p}}^e} \mathbf{e}_{p}

    which we solve for :math:`\mathbf{e_s}`. The total field :math:`\mathbf{e} = \mathbf{e_p} + \mathbf{e_s}`.

    The primary field is estimated from a background model (commonly as a 1D model).

    """

    # From FDEMproblem: Used to project the fields. Currently not used for NSEMproblem.
    _solutionType = ["e_pxSolution", "e_pySolution"]  # Forces order on the object
    _formulation = "EB"
    fieldsPair = Fields3DPrimarySecondary

    # Initiate properties
    _sigmaPrimary = None

    def __init__(self, mesh, **kwargs):
        super(Simulation3DPrimarySecondary, self).__init__(mesh, **kwargs)

    @property
    def sigmaPrimary(self):
        """
        A background model, use for the calculation of the primary fields.

        """
        return self._sigmaPrimary

    @sigmaPrimary.setter
    def sigmaPrimary(self, val):
        # Note: TODO add logic for val, make sure it is the correct size.
        self._sigmaPrimary = val

    def getA(self, freq):
        """
        Function to get the A system.

        :param float freq: Frequency
        :rtype: scipy.sparse.csr_matrix
        :return: A
        """
        Mfmui = self.MfMui
        Mesig = self.MeSigma
        C = self.mesh.edgeCurl

        return C.T * Mfmui * C + 1j * omega(freq) * Mesig

    def getADeriv(self, freq, u, v, adjoint=False):
        """
        Calculate the derivative of A wrt m.

        :param float freq: Frequency
        :param SimPEG.electromagnetics.frequency_domain.fields.FieldsFDEM u: NSEM Fields object
        :param numpy.ndarray v: vector of size (nU,) (adjoint=False)
            and size (nP,) (adjoint=True)
        :rtype: numpy.ndarray
        :return: Calculated derivative (nP,) (adjoint=False) and (nU,)[NOTE return as a (nU/2,2)
            columnwise polarizations] (adjoint=True) for both polarizations

        """
        # Fix u to be a matrix nE,2
        # This considers both polarizations and returns a nE,2 matrix for each polarization
        # The solution types
        sol0, sol1 = self._solutionType

        if adjoint:
            dMe_dsigV = self.MeSigmaDeriv(
                u[sol0], v[: self.mesh.nE], adjoint
            ) + self.MeSigmaDeriv(u[sol1], v[self.mesh.nE :], adjoint)
        else:
            # Need a nE,2 matrix to be returned
            dMe_dsigV = np.hstack(
                (
                    mkvc(self.MeSigmaDeriv(u[sol0], v, adjoint), 2),
                    mkvc(self.MeSigmaDeriv(u[sol1], v, adjoint), 2),
                )
            )
        return 1j * omega(freq) * dMe_dsigV

    def getRHS(self, freq):
        """
        Function to return the right hand side for the system.

        :param float freq: Frequency
        :rtype: numpy.ndarray
        :return: RHS for both polarizations, primary fields (nE, 2)

        """

        # Get sources for the frequncy(polarizations)
        Src = self.survey.get_sources_by_frequency(freq)[0]
        S_e = Src.S_e(self)
        return -1j * omega(freq) * S_e

    def getRHSDeriv(self, freq, v, adjoint=False):
        """
        The derivative of the RHS with respect to the model and the source

        :param float freq: Frequency
        :param numpy.ndarray v: vector of size (nU,) (adjoint=False)
            and size (nP,) (adjoint=True)
        :rtype: numpy.ndarray
        :return: Calculated derivative (nP,) (adjoint=False) and (nU,2) (adjoint=True)
            for both polarizations

        """

        # Note: the formulation of the derivative is the same for adjoint or not.
        Src = self.survey.get_sources_by_frequency(freq)[0]
        S_eDeriv = Src.S_eDeriv(self, v, adjoint)
        dRHS_dm = -1j * omega(freq) * S_eDeriv

        return dRHS_dm

    def fields(self, m=None):
        """
        Function to calculate all the fields for the model m.

        :param numpy.ndarray (nC,) m: Conductivity model
        :rtype: SimPEG.electromagnetics.frequency_domain.fields.FieldsFDEM
        :return: Fields object with of the solution

        """
        # Set the current model
        if m is not None:
            self.model = m

        F = self.fieldsPair(self)
        for freq in self.survey.frequencies:
            if self.verbose:
                startTime = time.time()
                print("Starting work for {:.3e}".format(freq))
                sys.stdout.flush()
            A = self.getA(freq)
            rhs = self.getRHS(freq)
            # Solve the system
            Ainv = self.solver(A, **self.solver_opts)
            e_s = Ainv * rhs

            # Store the fields
            Src = self.survey.get_sources_by_frequency(freq)[0]
            # Store the fields
            # Use self._solutionType
            F[Src, "e_pxSolution"] = e_s[:, 0]
            F[Src, "e_pySolution"] = e_s[:, 1]
            # Note curl e = -iwb so b = -curl/iw

            if self.verbose:
                print("Ran for {:f} seconds".format(time.time() - startTime))
                sys.stdout.flush()
            Ainv.clean()
        return F

    # def fields2(self, freq):
    #     """
    #     Function to calculate all the fields for the model m.
    #
    #     :param numpy.ndarray (nC,) m: Conductivity model
    #     :rtype: SimPEG.electromagnetics.frequency_domain.fields.FieldsFDEM
    #     :return: Fields object with of the solution
    #
    #     """
    #     """
    #     Function to calculate all the fields for the model m.
    #
    #     :param numpy.ndarray (nC,) m: Conductivity model
    #     :rtype: SimPEG.electromagnetics.frequency_domain.fields.FieldsFDEM
    #     :return: Fields object with of the solution
    #
    #     """
    #     A = self.getA(freq)
    #     rhs = self.getRHS(freq)
    #     # Solve the system
    #     Ainv = self.solver(A, **self.solver_opts)
    #     e_s = Ainv * rhs
    #
    #     # Store the fields
    #     # Src = self.survey.get_sources_by_frequency(freq)[0]
    #     # Store the fields
    #     # Use self._solutionType
    #     # self.F[Src, 'e_pxSolution'] = e_s[:, 0]
    #     # self.F[Src, 'e_pySolution'] = e_s[:, 1]
    #         # Note curl e = -iwb so b = -curl/iw
    #
    #     Ainv.clean()
    #     return e_s
    #
    # def fieldsMulti(self, freq):
    #     """
    #     Function to calculate all the fields for the model m.
    #
    #     :param numpy.ndarray (nC,) m: Conductivity model
    #     :rtype: SimPEG.electromagnetics.frequency_domain.fields.FieldsFDEM
    #     :return: Fields object with of the solution
    #
    #     """
    #     """
    #     Function to calculate all the fields for the model m.
    #
    #     :param numpy.ndarray (nC,) m: Conductivity model
    #     :rtype: SimPEG.electromagnetics.frequency_domain.fields.FieldsFDEM
    #     :return: Fields object with of the solution
    #
    #     """
    #     A = self.getA(freq)
    #     rhs = self.getRHS(freq)
    #     # Solve the system
    #     Ainv = self.solver(A, **self.solver_opts)
    #     e_s = Ainv * rhs
    #
    #     # Store the fields
    #     Src = self.survey.get_sources_by_frequency(freq)[0]
    #     # Store the fields
    #     # Use self._solutionType
    #     self.F[Src, 'e_pxSolution'] = e_s[:, 0]
    #     self.F[Src, 'e_pySolution'] = e_s[:, 1]
    #         # Note curl e = -iwb so b = -curl/iw
    #     Ainv.clean()
    #
    # def fieldsParallel(self, m=None):
    #     parallel = 'dask'
    #
    #     if m is not None:
    #         self.model = m
    #
    #     F = self.fieldsPair(self)
    #
    #     if parallel == 'dask':
    #         output = []
    #         f_ = dask.delayed(self.fields2, pure=True)
    #         for freq in self.survey.frequencies:
    #             output.append(da.from_delayed(f_(freq), (self.model.size, 2), dtype=float))
    #
    #         e_s = da.hstack(output).compute()
    #         cnt = 0
    #         for freq in self.survey.frequencies:
    #             index = cnt * 2
    #             # Store the fields
    #             Src = self.survey.get_sources_by_frequency(freq)[0]
    #             # Store the fields
    #             # Use self._solutionType
    #             F[Src, 'e_pxSolution'] = e_s[:, index]
    #             F[Src, 'e_pySolution'] = e_s[:, index + 1]
    #             cnt += 1
    #
    #     elif parallel == 'multipro':
    #         self.F = F
    #         pool = multiprocessing.Pool()
    #         pool.map(self.fieldsMulti, self.survey.frequencies)
    #         pool.close()
    #         pool.join()
    #
    #     return F


############
# Deprecated
############


@deprecate_class(removal_version="0.16.0", error=True)
class Problem3D_ePrimSec(Simulation3DPrimarySecondary):
    pass


@deprecate_class(removal_version="0.16.0", error=True)
class Problem1D_ePrimSec(Simulation1DPrimarySecondary):
    pass
