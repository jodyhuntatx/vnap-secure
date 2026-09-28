#ifndef SECURITY_HPP_FV13ZIYA
#define SECURITY_HPP_FV13ZIYA

#include <vanetza/common/position_provider.hpp>
#include <vanetza/common/runtime.hpp>
#include <vanetza/security/pseudonym_control.hpp>
#include <vanetza/security/security_entity.hpp>
#include <boost/program_options/options_description.hpp>
#include <boost/program_options/variables_map.hpp>
#include <memory>
#include "config.hpp"

std::unique_ptr<vanetza::security::SecurityEntity>
create_security_entity(const boost::program_options::variables_map&, vanetza::Runtime&, vanetza::PositionProvider&, config_t config_s = {});

/**
 * Pseudonym change interface of a security entity created by create_security_entity
 * \return interface if the entity uses a pseudonym pool (--pseudonym-certificate), otherwise nullptr
 */
vanetza::security::PseudonymControl* pseudonym_control(vanetza::security::SecurityEntity*);

void add_security_options(boost::program_options::options_description&);

#endif /* SECURITY_HPP_FV13ZIYA */

