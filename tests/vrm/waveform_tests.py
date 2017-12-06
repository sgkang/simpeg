import unittest
import SimPEG.VRM as VRM
import numpy as np


class VRM_waveform_tests(unittest.TestCase):

    def discrete_tests():

        times = np.logspace(-4, -2, 3)

        waveObj1 = VRM.WaveformVRM.SquarePulse(0.002)
        waveObj2 = VRM.WaveformVRM.ArbitraryDiscrete(np.r_[-0.00200001, -0.002, -0.0000000001, 0.], np.r_[0., 1., 1., 0.])
        waveObj3 = VRM.WaveformVRM.ArbitraryPiecewise(np.r_[-0.00200001, -0.002, -0.0000000001, 0.], np.r_[0., 1., 1., 0.])
        waveObj4 = VRM.WaveformVRM.Custom(times, waveObj1.getCharDecay('b', times))

        decay1b = waveObj1.getCharDecay('b', times)
        decay2b = waveObj2.getCharDecay('b', times)
        decay3b = waveObj3.getCharDecay('b', times)
        decay4b = waveObj4.getCharDecay()

        decay1dbdt = waveObj1.getCharDecay('dbdt', times)
        decay2dbdt = waveObj2.getCharDecay('dbdt', times)
        decay3dbdt = waveObj3.getCharDecay('dbdt', times)

        err1 = np.max(np.abs((decay2b-decay1b)/decay1b))
        err2 = np.max(np.abs((decay3b-decay1b)/decay1b))
        err3 = np.max(np.abs((decay4b-decay1b)/decay1b))
        err4 = np.max(np.abs((decay2dbdt-decay1dbdt)/decay1dbdt))
        err5 = np.max(np.abs((decay3dbdt-decay1dbdt)/decay1dbdt))

        self.assertTrue(err1 < 0.01 and err2 < 0.01 and err3 < 0.01 and err4 < 0.01 and err5 < 0.01)

    def loguniform_tests():

        times = np.logspace(-4, -2, 3)

        waveObj1 = VRM.WaveformVRM.StepOff()
        waveObj2 = VRM.WaveformVRM.SquarePulse(0.02)

        chi0 = np.array([0.])
        dchi = np.array([0.01])
        tau1 = np.array([1e-10])
        tau2 = np.array([1e3])

        decay1b = (dchi/np.log(tau2/tau1))*waveObj2.getCharDecay('b', times)
        decay2b = waveObj2.getLogUniformDecay('b', times, chi0, dchi, tau1, tau2)

        decay1dbdt = (dchi/np.log(tau2/tau1))*waveObj1.getCharDecay('dbdt', times)
        decay2dbdt = waveObj1.getLogUniformDecay('dbdt', times, chi0, dchi, tau1, tau2)
        decay3dbdt = (dchi/np.log(tau2/tau1))*waveObj2.getCharDecay('dbdt', times)
        decay4dbdt = waveObj2.getLogUniformDecay('dbdt', times, chi0, dchi, tau1, tau2)

        err1 = np.max(np.abs((decay2b-decay1b)/decay1b))
        err2 = np.max(np.abs((decay2dbdt-decay1dbdt)/decay1dbdt))
        err3 = np.max(np.abs((decay4dbdt-decay3dbdt)/decay3dbdt))

        self.assertTrue(err1 < 0.01 and err2 < 0.01 and err3 < 0.01)

if __name__ == '__main__':
    unittest.main()
