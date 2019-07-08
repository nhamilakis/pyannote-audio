#!/usr/bin/env python
# encoding: utf-8

# The MIT License (MIT)

# Copyright (c) 2016-2018 CNRS

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# AUTHORS
# Hervé BREDIN - http://herve.niderb.fr

import warnings
import inspect
import numpy as np

from .utils import RawAudio
from .utils import get_audio_duration

from pyannote.core import Segment
from pyannote.core import SlidingWindow
from pyannote.core import SlidingWindowFeature

from pyannote.database import get_unique_identifier

from librosa.util import valid_audio
from librosa.util.exceptions import ParameterError


class FeatureExtraction(object):
    """Base class for feature extraction

    Parameters
    ----------
    augmentation : `pyannote.audio.augmentation.Augmentation`, optional
        Data augmentation.
    sample_rate : int, optional
        Convert audio to use this sample rate.

    See also
    --------
    `pyannote.audio.augmentation.AddNoise`
    """

    def __init__(self, augmentation=None, sample_rate=None):
        super().__init__()
        self.sample_rate = sample_rate

        # used in FeatureExtraction.crop
        self.raw_audio_ = RawAudio(
            sample_rate=self.sample_rate, mono=True,
            augmentation=augmentation)

    def get_dimension(self):
        """Get dimension of feature vectors

        Returns
        -------
        dimension : int
            Dimension of feature vectors
        """
        msg = ('`FeatureExtraction subclasses must implement '
               '`get_dimension` method.')
        raise NotImplementedError(msg)

    @property
    def dimension(self):
        """Dimension of feature vectors"""
        return self.get_dimension()

    def get_sliding_window(self):
        """Get sliding window used for feature extraction

        Returns
        -------
        sliding_window : `pyannote.core.SlidingWindow`
            Sliding window used for feature extraction.
        """

        msg = ('`FeatureExtraction` subclasses must implement '
               '`get_sliding_window` method.')
        raise NotImplementedError(msg)

    @property
    def sliding_window(self):
        """Sliding window used for feature extraction"""
        return self.get_sliding_window()

    def get_features(self, y, sample_rate):
        """Extract features from waveform

        Parameters
        ----------
        y : (n_samples, 1) numpy array
            Waveform.
        sample_rate : int
            Sample rate.
        epoch: int
            Current epoch

        Returns
        -------
        features : (n_frames, dimension) numpy array
            Extracted features
        """
        msg = ('`FeatureExtractions subclasses must implement '
               '`get_features` method.')
        raise NotImplementedError(msg)

    def __call__(self, current_file):
        """Extract features from file

        Parameters
        ----------
        current_file : dict
            `pyannote.database` files.

        Returns
        -------
        features : `pyannote.core.SlidingWindowFeature`
            Extracted features
        """

        # load waveform, re-sample, convert to mono, augment, normalize
        y, sample_rate = self.raw_audio_(current_file, return_sr=True)

        # compute features
        features = self.get_features(y.data, sample_rate)

        # basic quality check
        if np.any(np.isnan(features)):
            uri = get_unique_identifier(current_file)
            msg = f'Features extracted from "{uri}" contain NaNs.'
            warnings.warn(msg.format(uri=uri))

        # wrap features in a `SlidingWindowFeature` instance
        return SlidingWindowFeature(features, self.sliding_window)

    def get_context_duration(self):
        """

        TODO. explain why this is needed

        Returns
        -------
        context : float
            Context duration, in seconds.
        """
        return 0.

    def crop(self, current_file, segment, mode='center', fixed=None, epoch=None):
        """Fast version of self(current_file).crop(segment, mode='center',
+                                                  fixed=segment.duration)

        Parameters
        ----------
        current_file : dict
            `pyannote.database` file. Must contain a 'duration' key that
            provides the duration (in seconds) of the audio file.
        segment : `pyannote.core.Segment`
            Segment from which to extract features.

        Returns
        -------
        features : (n_frames, dimension) numpy array
            Extracted features

        See also
        --------
        `pyannote.core.SlidingWindowFeature.crop`
        """

        # get_audio_duration can be very long for some file
        # therefore, it should be computed once and for all
        if 'duration' not in current_file:
            msg = ('`FeatureExtraction.crop` method expects `current_file` to '
                   'contain a precomputed "duration" key.')
            raise ValueError(msg)
        duration = current_file['duration']

        context = self.get_context_duration()

        # extend segment on both sides with requested context
        xsegment = Segment(max(0, segment.start - context),
                           min(duration, segment.end + context))

        # obtain (augmented) waveform on this extended segment
        y = self.raw_audio_.crop(current_file, xsegment, mode='center',
                                 fixed=xsegment.duration, epoch=epoch)

        if "epoch" in inspect.signature(self.get_features).parameters:
            features = self.get_features(y, self.sample_rate, epoch)
        else:
            features = self.get_features(y, self.sample_rate)

        # get rid of additional context before returning
        frames = self.sliding_window
        shifted_frames = SlidingWindow(start=xsegment.start - frames.step,
                                       step=frames.step,
                                       duration=frames.duration)
        (start, end), = shifted_frames.crop(segment, mode=mode, fixed=fixed,
                                            return_ranges=True)
        return features[start:end]
