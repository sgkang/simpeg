from __future__ import division, print_function
import unittest
import numpy as np
import discretize
from SimPEG import maps, SolverLU
from SimPEG.electromagnetics import time_domain as tdem
from SimPEG.electromagnetics import analytics
from scipy.constants import mu_0
import matplotlib.pyplot as plt
from pymatsolver import Pardiso as Solver


def halfSpaceProblemAnaDiff(
    meshType,
    srctype="MagDipole",
    sig_half=1e-2,
    rxOffset=50.0,
    bounds=None,
    plotIt=False,
    rxType="MagneticFluxDensityz",
):
    if bounds is None:
        bounds = [1e-5, 1e-3]
    if meshType == "CYL":
        cs, ncx, ncz, npad = 5.0, 30, 10, 15
        hx = [(cs, ncx), (cs, npad, 1.3)]
        hz = [(cs, npad, -1.3), (cs, ncz), (cs, npad, 1.3)]
        mesh = discretize.CylMesh([hx, 1, hz], "00C")

    elif meshType == "TENSOR":
        cs, nc, npad = 20.0, 13, 5
        hx = [(cs, npad, -1.3), (cs, nc), (cs, npad, 1.3)]
        hy = [(cs, npad, -1.3), (cs, nc), (cs, npad, 1.3)]
        hz = [(cs, npad, -1.3), (cs, nc), (cs, npad, 1.3)]
        mesh = discretize.TensorMesh([hx, hy, hz], "CCC")

    active = mesh.vectorCCz < 0.0
    actMap = maps.InjectActiveCells(mesh, active, np.log(1e-8), nC=mesh.nCz)
    mapping = maps.ExpMap(mesh) * maps.SurjectVertical1D(mesh) * actMap

    rx = getattr(tdem.Rx, "Point{}".format(rxType[:-1]))(
        np.array([[rxOffset, 0.0, 0.0]]), np.logspace(-5, -4, 21), rxType[-1]
    )

    if srctype == "MagDipole":
        src = tdem.Src.MagDipole(
            [rx],
            waveform=tdem.Src.StepOffWaveform(),
            location=np.array([0.0, 0.0, 0.0]),
        )
    elif srctype == "CircularLoop":
        src = tdem.Src.CircularLoop(
            [rx],
            waveform=tdem.Src.StepOffWaveform(),
            location=np.array([0.0, 0.0, 0.0]),
            radius=0.1,
        )

    survey = tdem.Survey([src])

    time_steps = [
        (1e-06, 40),
        (5e-06, 40),
        (1e-05, 40),
        (5e-05, 40),
        (0.0001, 40),
        (0.0005, 40),
    ]

    prb = tdem.Simulation3DMagneticFluxDensity(
        mesh, survey=survey, time_steps=time_steps, sigmaMap=mapping
    )
    prb.solver = Solver

    sigma = np.ones(mesh.nCz) * 1e-8
    sigma[active] = sig_half
    sigma = np.log(sigma[active])
    if srctype == "MagDipole":
        bz_ana = mu_0 * analytics.hzAnalyticDipoleT(
            rx.locations[0][0] + 1e-3, rx.times, sig_half
        )
    elif srctype == "CircularLoop":
        bz_ana = mu_0 * analytics.hzAnalyticDipoleT(13, rx.times, sig_half)

    bz_calc = prb.dpred(sigma)
    ind = np.logical_and(rx.times > bounds[0], rx.times < bounds[1])
    log10diff = np.linalg.norm(
        np.log10(np.abs(bz_calc[ind])) - np.log10(np.abs(bz_ana[ind]))
    ) / np.linalg.norm(np.log10(np.abs(bz_ana[ind])))

    print(
        " |bz_ana| = {ana} |bz_num| = {num} |bz_ana-bz_num| = {diff}".format(
            ana=np.linalg.norm(bz_ana),
            num=np.linalg.norm(bz_calc),
            diff=np.linalg.norm(bz_ana - bz_calc),
        )
    )
    print("Difference: {}".format(log10diff))

    if plotIt is True:
        plt.loglog(
            rx.times[bz_calc > 0],
            bz_calc[bz_calc > 0],
            "r",
            rx.times[bz_calc < 0],
            -bz_calc[bz_calc < 0],
            "r--",
        )
        plt.loglog(rx.times, abs(bz_ana), "b*")
        plt.title("sig_half = {0:e}".format(sig_half))
        plt.show()

    return log10diff


class TDEM_SimpleSrcTests(unittest.TestCase):
    def test_source(self):
        waveform = tdem.Src.StepOffWaveform()
        assert waveform.eval(0.0) == 1.0


class TDEM_bTests(unittest.TestCase):
    def test_analytic_p2_CYL_50_MagDipolem(self):
        self.assertTrue(
            halfSpaceProblemAnaDiff("CYL", rxOffset=50.0, sig_half=1e2) < 0.01
        )

    def test_analytic_p1_CYL_50_MagDipolem(self):
        self.assertTrue(
            halfSpaceProblemAnaDiff("CYL", rxOffset=50.0, sig_half=1e1) < 0.01
        )

    def test_analytic_p0_CYL_50_MagDipolem(self):
        self.assertTrue(
            halfSpaceProblemAnaDiff("CYL", rxOffset=50.0, sig_half=1e0) < 0.01
        )

    def test_analytic_m1_CYL_50_MagDipolem(self):
        self.assertTrue(
            halfSpaceProblemAnaDiff("CYL", rxOffset=50.0, sig_half=1e-1) < 0.01
        )

    def test_analytic_m2_CYL_50_MagDipolem(self):
        self.assertTrue(
            halfSpaceProblemAnaDiff("CYL", rxOffset=50.0, sig_half=1e-2) < 0.01
        )

    def test_analytic_m3_CYL_50_MagDipolem(self):
        self.assertTrue(
            halfSpaceProblemAnaDiff("CYL", rxOffset=50.0, sig_half=1e-3) < 0.02
        )

    def test_analytic_p0_CYL_1m_MagDipole(self):
        self.assertTrue(
            halfSpaceProblemAnaDiff("CYL", rxOffset=1.0, sig_half=1e0) < 0.01
        )

    def test_analytic_m1_CYL_1m_MagDipole(self):
        self.assertTrue(
            halfSpaceProblemAnaDiff("CYL", rxOffset=1.0, sig_half=1e-1) < 0.01
        )

    def test_analytic_m2_CYL_1m_MagDipole(self):
        self.assertTrue(
            halfSpaceProblemAnaDiff("CYL", rxOffset=1.0, sig_half=1e-2) < 0.01
        )

    def test_analytic_m3_CYL_1m_MagDipole(self):
        self.assertTrue(
            halfSpaceProblemAnaDiff("CYL", rxOffset=1.0, sig_half=1e-3) < 0.02
        )

    def test_analytic_p0_CYL_0m_CircularLoop(self):
        self.assertTrue(
            halfSpaceProblemAnaDiff(
                "CYL", srctype="CircularLoop", rxOffset=0.0, sig_half=1e0
            )
            < 0.15
        )

    def test_analytic_m1_CYL_0m_CircularLoop(self):
        self.assertTrue(
            halfSpaceProblemAnaDiff(
                "CYL", srctype="CircularLoop", rxOffset=0.0, sig_half=1e-1
            )
            < 0.15
        )

    def test_analytic_m2_CYL_0m_CircularLoop(self):
        self.assertTrue(
            halfSpaceProblemAnaDiff(
                "CYL", srctype="CircularLoop", rxOffset=0.0, sig_half=1e-2
            )
            < 0.15
        )

    def test_analytic_m3_CYL_0m_CircularLoop(self):
        self.assertTrue(
            halfSpaceProblemAnaDiff(
                "CYL", srctype="CircularLoop", rxOffset=0.0, sig_half=1e-3
            )
            < 0.15
        )


if __name__ == "__main__":
    unittest.main()
