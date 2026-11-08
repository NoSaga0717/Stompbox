#pragma once

#include <filesystem>

#if _WIN32
#include "windows.h"
#endif

#include <math.h>
#include <algorithm>
#include <memory.h>
#include <sys/stat.h>
#include <fstream>
#include <limits.h>

#include "StompBox.h"
#include "FileType.h"

#include "NeuralAudio/NeuralModel.h"

// Capture levels read from the model metadata (NAN when the model doesn't say)
struct NAMModelLevels
{
	NeuralAudio::NeuralModel* Model = nullptr;
	float InputDBu = NAN;
	float OutputDBu = NAN;
};

class NAMLoader : public FileLoader<NeuralAudio::NeuralModel>
{
protected:
	NeuralAudio::NeuralModel* LoadFromFile(const std::filesystem::path& loadPath);

public:
	NAMLoader(const FileType& fileType) :
		FileLoader<NeuralAudio::NeuralModel>(fileType)
	{
	}

	// Allocation-free lookup, safe to call from the audio thread
	NAMModelLevels GetLevels(NeuralAudio::NeuralModel* model) const;

private:
	NeuralAudio::NeuralModelLoader modelLoader;

	// Levels of the last few loaded models. Written when a model is loaded (before it is handed
	// to the audio thread), read by the audio thread when the model changes.
	static const int NumLevelSlots = 4;
	NAMModelLevels levelSlots[NumLevelSlots];
	int nextLevelSlot = 0;
};

enum
{
	NAM_OUTPUT_NORMALIZED,
	NAM_OUTPUT_CALIBRATED
};

class NAM : public StompBox
{
private:
	float modelIndex = -1;
	float prevDCInput = 0;
	double prevDCOutput = 0;
	float outputMode = NAM_OUTPUT_NORMALIZED;
	FileType fileType;
	NAMLoader namLoader;
	NeuralAudio::NeuralModel* levelsModel = nullptr;
	NAMModelLevels modelLevels;

public:

	NAM(const std::string folderName, const std::vector<std::string> fileExtensions, const std::filesystem::path& basePath);
	virtual ~NAM() {}
	virtual void init(int samplingFreq) override;
	void SetParameterValue(StompBoxParameter& parameter, float value) override;
	bool RescanFiles() override;
	virtual void compute(int count, float* input, float* output) override;
};
